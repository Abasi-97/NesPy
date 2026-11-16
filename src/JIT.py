import time
import traceback

from cpu import CPU


class AsyncSuperblockJIT:
    """
    Superblock JIT for the 6502 core.

    - get_superblock(pc): returns a compiled function for pc (compiles on a cache miss).
    - invalidate(addr) / invalidate_pages(lo, hi): drop blocks built from those pages.

    The old background compile thread was removed: nothing ever queued work for it, and a thread
    that wakes up ten times a second only competes with the emulation loop for the GIL. The class
    keeps its name so existing imports keep working.
    """

    MAX_INSTR = 256

    def __init__(self, cpu_instance: CPU, max_chained_blocks=10):
        self.cpu = cpu_instance
        self.max_chained_blocks = max_chained_blocks

        # One block cache per PRG bank layout ("mapping"). Switching banks swaps to the cache for the
        # new layout instead of throwing compiled code away, so a game that flips between a handful
        # of banks every frame compiles each block once per layout instead of every frame.
        self.sets = {}
        self.cache = {}            # current layout: start_pc -> compiled function
        self.page_blocks = {}      # current layout: page -> set(start_pc)
        self.block_pages = {}      # current layout: start_pc -> set(page)
        self._sig = None
        self._select(getattr(cpu_instance.bus, 'prg_signature', None))

        self.compile_count = 0
        self.compile_time = 0.0

        self.cpu.jit = self
        self.cpu.smc = False       # set by invalidate(); compiled blocks check it after stores

    # ------------------------------------------------------------------ API

    def get_superblock(self, start_pc):
        func = self.cache.get(start_pc)
        if func is not None:
            return func
        try:
            t0 = time.perf_counter()
            func, pages = self._compile_superblock(start_pc)
            self.compile_time += time.perf_counter() - t0
            self.compile_count += 1
            self._install(start_pc, func, pages)
            return func
        except NotImplementedError as e:
            print(f"Compilation error at 0x{start_pc:04X}: {e}")
            return None
        except Exception as e:
            traceback.print_exc()
            print(f"Compilation error at 0x{start_pc:04X}: {e!r}")
            return None

    def request_compile(self, start_pc):
        """Kept for compatibility: compile now."""
        self.get_superblock(start_pc & 0xFFFF)

    MAX_SETS = 64

    def _select(self, sig):
        s = self.sets.get(sig)
        if s is None:
            if len(self.sets) >= self.MAX_SETS:      # runaway number of layouts: start over
                self.sets.clear()
            s = self.sets[sig] = ({}, {}, {})
        self._sig = sig
        self.cache, self.page_blocks, self.block_pages = s

    def set_mapping(self, sig):
        """Called by the Bus when the PRG bank layout changes. Swaps block caches, drops nothing."""
        if sig == self._sig:
            return
        self.cpu.smc = True       # the block that performed the bank write must stop right after it
        self._select(sig)

    def invalidate(self, addr):
        """Drop every cached block (in every layout) that was built from the page containing addr."""
        page = self._page_of(addr)
        self.cpu.smc = True   # a block that is running right now may have just been made stale
        for cache, page_blocks, block_pages in self.sets.values():
            for start in page_blocks.pop(page, ()):
                cache.pop(start, None)
                for p in block_pages.pop(start, ()):
                    if p != page:
                        peers = page_blocks.get(p)
                        if peers:
                            peers.discard(start)
                            if not peers:
                                del page_blocks[p]
        if page < 0x80:
            self.cpu.code_pages[page] = 0

    def invalidate_pages(self, lo, hi):
        for p in range(lo, hi + 1):
            self.invalidate(p << 8)

    def shutdown(self):
        pass

    # ------------------------------------------------------------- internals

    def _install(self, start_pc, func, pages):
        for p in pages:
            if p < 0x80:
                self.cpu.code_pages[p] = 1
        self.cache[start_pc] = func
        self.block_pages[start_pc] = pages
        for p in pages:
            self.page_blocks.setdefault(p, set()).add(start_pc)

    def _compile_superblock(self, start_pc):
        read = self.cpu.read_byte
        lines = ["def compiled_superblock(cpu):"]
        total_cycles = 0
        pc = start_pc
        visited = set()
        pages = set()
        chain = 0
        count = 0
        table = CPU.DECODE_TABLE

        while True:
            pc &= 0xFFFF
            if count >= self.MAX_INSTR or pc in visited:
                lines.append(f"    cpu.PC = 0x{pc:04X}")
                break

            visited.add(pc)

            opcode = read(pc)
            entry = table.get(opcode)
            if not entry:
                if count == 0:
                    raise NotImplementedError(
                        f"Opcode 0x{opcode:02X} at PC 0x{pc:04X} unsupported.")
                lines.append(f"    cpu.PC = 0x{pc:04X}")  # let the caller deal with it
                break

            length = entry['length']
            for i in range(length):
                pages.add(self._page_of(pc + i))

            op0 = read(pc + 1) if length > 1 else 0
            op1 = read(pc + 2) if length > 2 else 0
            target = op0 | (op1 << 8)

            offset = op0 - 256 if op0 & 0x80 else op0
            nxt = (pc + length) & 0xFFFF
            taken = (nxt + offset) & 0xFFFF
            extra = 1 + int((nxt & 0xFF00) != (taken & 0xFF00))  # +1 taken, +1 on page cross

            template = entry['code']
            if target < 0x2000 and 'code_ram' in entry:
                template = entry['code_ram']          # absolute address inside internal RAM
            rt = target & 0x7FF
            code = template.format(op0=op0, op1=op1, target=target, pc=pc, next=nxt,
                                   taken=taken, extra=extra, rt=rt, rp=rt >> 8)
            for line in code.split('\n'):
                if line:
                    lines.append(f"    {line}")

            total_cycles += entry['cycles']
            count += 1

            # Self-modifying code: if this instruction wrote memory and that invalidated compiled
            # code (cpu.smc), the rest of the block may be stale. Leave right here and let the
            # dispatcher recompile from the next instruction.
            if not entry['JUMP'] and ('write_byte(' in template or 'push_stack(' in template):
                lines.append("    if cpu.smc:")
                lines.append("        cpu.smc = False")
                lines.append(f"        cpu.PC = 0x{nxt:04X}")
                lines.append(f"        cpu.cycles += {total_cycles}")
                lines.append("        return")

            if opcode == 0x4C:  # JMP abs: follow inline if possible
                lines.pop()     # drop the "cpu.PC = target" emitted above; decide here
                if target in visited or chain >= self.max_chained_blocks:
                    lines.append(f"    cpu.PC = 0x{target:04X}")
                    break
                chain += 1
                lines.append(f"    # -> chained to 0x{target:04X}")
                pc = target
                continue

            if entry['JUMP']:   # branches, JMP ind, JSR, RTS, RTI, BRK set cpu.PC themselves
                break

            pc += length

        lines.append(f"    cpu.cycles += {total_cycles}")
        code_str = "\n".join(lines)
        namespace = {'ram': self.cpu.ram, 'cp': self.cpu.code_pages}
        exec(compile(code_str, f"<superblock_0x{start_pc:04X}>", "exec"), namespace)
        return namespace['compiled_superblock'], pages

    @staticmethod
    def _page_of(addr):
        page = (addr & 0xFFFF) >> 8
        return page & 7 if page < 0x20 else page