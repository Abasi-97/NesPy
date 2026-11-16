class Controller:
    # bit 0=A, 1=B, 2=Select, 3=Start, 4=Up, 5=Down, 6=Left, 7=Right

    def __init__(self):
        self.buttons = 0
        self.shift = 0
        self.strobe = False
        self.writes = 0          # diagnostics: how often the game strobes / reads the joypad
        self.reads = 0

    def write(self, value):
        self.writes += 1
        self.strobe = bool(value & 1)
        if self.strobe:
            self.shift = self.buttons

    def read(self):
        self.reads += 1
        if self.strobe:
            return self.buttons & 1
        bit = self.shift & 1
        self.shift = (self.shift >> 1) | 0x80
        return bit


class Bus:
    """
    CPU address space. Each 256-byte page is bound either to a memoryview (direct byte access)
    or to a handler function. Views and handlers live in separate tables so read()/write() do
    not need an isinstance() check on every access.
    """

    def __init__(self, cpu, ppu) -> None:
        self.cpu = cpu
        self.ppu = ppu
        self.ram = bytearray(2048)
        cpu.ram = self.ram                    # the CPU/JIT access internal RAM directly
        if ppu is not None:
            ppu.cpu = cpu                     # lets the PPU timestamp mid-frame register writes
        self.controller = Controller()

        self.rd_mv = [None] * 256
        self.rd_fn = [None] * 256
        self.wr_mv = [None] * 256
        self.wr_fn = [None] * 256

        self._prg_page_offsets = [None] * 256
        self._prg_view = None
        self._mapper = None
        self.prg_signature = None        # identifies the current PRG bank layout (used by the JIT)

        self.init_memory_map()
        self.mapper = None

    # ---------------------------------------------------------------- binding
    def _bind_read(self, page, target):
        if isinstance(target, memoryview):
            self.rd_mv[page], self.rd_fn[page] = target, None
        else:
            self.rd_mv[page], self.rd_fn[page] = None, target

    def _bind_write(self, page, target):
        if isinstance(target, memoryview):
            self.wr_mv[page], self.wr_fn[page] = target, None
        else:
            self.wr_mv[page], self.wr_fn[page] = None, target

    def init_memory_map(self):
        ram_view = memoryview(self.ram)
        for page in range(0x00, 0x20):
            p = page & 0x07
            view = ram_view[p << 8:(p + 1) << 8]
            self._bind_read(page, view)
            self._bind_write(page, view)
        for page in range(0x20, 0x40):
            self._bind_read(page, self._read_ppu_page)
            self._bind_write(page, self._write_ppu_page)
        for page in range(0x40, 0x42):
            self._bind_read(page, self._read_io_page)
            self._bind_write(page, self._write_io_page)
        for page in range(0x42, 0x80):
            self._bind_read(page, self._read_open)
            self._bind_write(page, self._write_open)
        for page in range(0x80, 0x100):
            self._bind_read(page, self._read_rom_page)
            self._bind_write(page, self._write_rom_page)

    @property
    def mapper(self):
        return self._mapper

    @mapper.setter
    def mapper(self, m):
        self._mapper = m
        self._prg_page_offsets = [None] * 256
        self._prg_view = None

        for page in range(0x60, 0x80):
            self._bind_read(page, self._read_open)
            self._bind_write(page, self._write_open)

        if m:
            self._prg_view = memoryview(m.prg_rom)
            m.on_bank_change = self.update_cartridge_mapping
            if getattr(m, 'prg_ram', None) is not None:
                ram = memoryview(m.prg_ram)
                for page in range(0x60, 0x80):
                    view = ram[(page - 0x60) << 8:(page - 0x5F) << 8]
                    self._bind_read(page, view)
                    self._bind_write(page, view)
            if self.ppu is not None and hasattr(m, 'attach_ppu'):
                m.attach_ppu(self.ppu)
        self.update_cartridge_mapping()

    def update_cartridge_mapping(self):
        """Re-slice PRG pages after a bank change; invalidate JIT blocks only on pages that changed."""
        m = self._mapper
        if not m:
            return
        new = [None] * 256
        for start, size, offset in m.prg_windows():
            first = start >> 8
            for i in range(size >> 8):
                new[first + i] = offset + (i << 8)

        old = self._prg_page_offsets
        changed = []
        for page in range(0x80, 0x100):
            if new[page] == old[page]:
                continue
            changed.append(page)
            off = new[page]
            if off is None or off + 256 > len(m.prg_rom):
                self._bind_read(page, self._read_rom_page)
            else:
                self._bind_read(page, self._prg_view[off:off + 256])
            self._bind_write(page, self._write_rom_page)
        self._prg_page_offsets = new
        self.prg_signature = tuple(new[0x80:])

        jit = getattr(self.cpu, 'jit', None)
        if jit and changed:
            jit.set_mapping(self.prg_signature)    # swap to that layout's compiled blocks

    def tick(self, cpu_cycles: int):
        return self.ppu.tick(cpu_cycles) if self.ppu else False

    # ------------------------------------------------------------ memory access
    def read(self, address: int) -> int:
        address &= 0xFFFF
        page = address >> 8
        mv = self.rd_mv[page]
        if mv is not None:
            return mv[address & 0xFF]
        return self.rd_fn[page](address)

    def write(self, address: int, value: int):
        address &= 0xFFFF
        page = address >> 8
        mv = self.wr_mv[page]
        if mv is not None:
            mv[address & 0xFF] = value & 0xFF
        else:
            self.wr_fn[page](address, value & 0xFF)

    # --------------------------------------------------------------- handlers
    def _read_ppu_page(self, address: int) -> int:
        return self.ppu.read_register(address & 7)

    def _write_ppu_page(self, address: int, value: int):
        self.ppu.write_register(address & 7, value)

    def _read_io_page(self, address: int) -> int:
        if address == 0x4016:
            return self.controller.read() | 0x40
        return 0

    def _write_io_page(self, address: int, value: int):
        if address == 0x4014:
            base = value << 8
            if base < 0x2000:
                b = base & 0x7FF
                data = self.ram[b:b + 256]
            else:
                data = [self.read(base + i) for i in range(256)]
            self.ppu.oam_dma(data)
            self.cpu.cycles += 513
        elif address == 0x4016:
            self.controller.write(value)

    def _read_rom_page(self, address: int) -> int:
        return self._mapper.read_prg(address) if self._mapper else 0

    def _write_rom_page(self, address: int, value: int):
        if self._mapper:
            self._mapper.write_prg(address, value)

    def _read_open(self, address): return 0
    def _write_open(self, address, value): pass

    def read_mem(self, address: int) -> int:
        return self.read(address)

    def write_mem(self, address: int, value: int):
        self.write(address, value)