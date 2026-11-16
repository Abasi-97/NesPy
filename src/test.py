"""
Run Klaus Dormann's 6502 functional test through the superblock JIT
on a flat 64KB RAM (no NES bus, no PPU, no mappers).

Put 6502_functional_test.bin in the same folder as this script, then run it.
Optional args: [filename] [max_mcycles]
"""
import os
import sys
import time

from cpu import CPU
from JIT import AsyncSuperblockJIT

LOAD_ADDR = 0x0000
START_PC = 0x0400
SUCCESS_PC = 0x3469      # "success" trap in the standard 6502_functional_test.bin


def _flat_write_byte(self, address, value):
    """Benchmark-only write: no RAM mirroring (flat 64KB), same JIT invalidation rule."""
    address &= 0xFFFF
    mem = self.bus.mem
    if self.code_pages[address >> 8] and mem[address] != (value & 0xFF):
        self.jit.invalidate(address)
    mem[address] = value & 0xFF


class FlatBus:
    """Duck-typed stand-in for Bus: 64KB of plain RAM, no mirroring, no IO."""
    __slots__ = ("mem",)

    def __init__(self):
        self.mem = bytearray(0x10000)

    def read(self, address):
        return self.mem[address & 0xFFFF]

    def write(self, address, value):
        self.mem[address & 0xFFFF] = value & 0xFF


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    name = sys.argv[1] if len(sys.argv) > 1 else "6502_functional_test.bin"
    path = name if os.path.isabs(name) else os.path.join(here, name)
    max_cycles = int(float(sys.argv[2]) * 1_000_000) if len(sys.argv) > 2 else 200_000_000

    print("Looking for:", path)
    if not os.path.exists(path):
        print("Not found. Files in that folder:", os.listdir(here))
        return

    bus = FlatBus()
    with open(path, "rb") as f:
        data = f.read()
    bus.mem[LOAD_ADDR:LOAD_ADDR + len(data)] = data
    print(f"Loaded {len(data):,} bytes (expected 65,536)")

    # The NES code mirrors $0000-$1FFF every 8 pages. On a flat 64KB RAM that aliasing would make
    # zero-page/stack writes constantly invalidate compiled code at $0800+, so turn it off here.
    AsyncSuperblockJIT._page_of = staticmethod(lambda addr: (addr & 0xFFFF) >> 8)
    CPU.write_byte = _flat_write_byte

    cpu = CPU(bus)
    cpu.read_byte = bus.mem.__getitem__   # fastest possible read: skip the bus method call
    jit = AsyncSuperblockJIT(cpu)
    cpu.PC = START_PC

    cache = jit.cache            # direct dict lookup in the hot loop
    blocks = 0
    status = "cycle limit reached"

    t0 = time.perf_counter()
    last_report = t0
    while cpu.cycles < max_cycles:
        pc = cpu.PC
        func = cache.get(pc)
        if func is None:
            func = jit.get_superblock(pc)
            if func is None:
                status = f"compile failed at ${pc:04X} (opcode ${bus.mem[pc]:02X})"
                break

        c0 = cpu.cycles
        func(cpu)
        blocks += 1

        # Trap detection: JMP * / Bxx * jump to themselves in <=4 cycles.
        if cpu.PC == pc and cpu.cycles - c0 <= 4:
            status = "SUCCESS" if pc == SUCCESS_PC else f"TRAPPED (test failure) at ${pc:04X}"
            break

        if not (blocks & 0xFFFF):
            now = time.perf_counter()
            if now - last_report > 2.0:
                last_report = now
                print(f"  ... PC=${cpu.PC:04X}  {cpu.cycles / 1e6:.1f}M cycles", flush=True)

    elapsed = time.perf_counter() - t0
    jit.shutdown()

    mhz = cpu.cycles / elapsed / 1e6 if elapsed > 0 else 0.0
    print()
    print(f"Result        : {status}")
    print(f"Final PC      : ${cpu.PC:04X}")
    print(f"Wall time     : {elapsed:.2f} s")
    print(f"6502 cycles   : {cpu.cycles:,}")
    print(f"Speed         : {mhz:.2f} MHz emulated  ({mhz / 1.789773:.2f}x real NES CPU)")
    print(f"Blocks run    : {blocks:,}  (avg {cpu.cycles / max(blocks, 1):.1f} cycles/block)")
    print(f"Compiled      : {jit.compile_count} blocks in {jit.compile_time:.2f} s "
          f"({100 * jit.compile_time / max(elapsed, 1e-9):.1f}% of wall time)")


if __name__ == "__main__":
    main()