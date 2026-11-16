class Mapper:
    """
    Base cartridge mapper.

    CHR is exposed to the PPU as `chr_window`: one flat 8KB buffer the mapper keeps up to date
    (copying banks into it when they change), so the PPU never has to call back per tile.
    """

    def __init__(self, prg_rom: bytes, chr_rom: bytes, mirroring=None) -> None:
        self.prg_rom = prg_rom
        self.chr_rom = chr_rom
        self.has_chr_ram = len(chr_rom) == 0
        self.chr_ram = bytearray(0x2000) if self.has_chr_ram else None
        self.chr_window = self.chr_ram if self.has_chr_ram else chr_rom
        # None = keep the mirroring the PPU was created with (the iNES header value)
        # 0 = horizontal, 1 = vertical, 2 = one-screen (lower), 3 = one-screen (upper)
        self.mirroring = mirroring
        self.prg_ram = None              # bytearray at $6000-$7FFF if the mapper has PRG-RAM
        self.on_bank_change = None       # set by the Bus; called when the PRG mapping changes
        self.ppu = None

    def prg_windows(self):
        """[(cpu_start, size, offset_into_prg_rom), ...] covering $8000-$FFFF. Used by the Bus."""
        raise NotImplementedError

    def read_prg(self, address: int) -> int:
        for start, size, offset in self.prg_windows():
            if start <= address < start + size:
                return self.prg_rom[(offset + address - start) % len(self.prg_rom)]
        return 0

    def write_prg(self, address: int, value: int):
        pass

    def read_chr(self, address: int) -> int:
        return self.chr_window[address & 0x1FFF]

    def write_chr(self, address: int, value: int):
        if self.has_chr_ram:
            self.chr_window[address & 0x1FFF] = value

    def attach_ppu(self, ppu):
        self.ppu = ppu
        ppu.chr_rom = self.chr_window
        ppu.chr_writable = self.has_chr_ram
        if self.mirroring is not None:
            ppu.mirroring = self.mirroring
        ppu.chr_version += 1

    def _notify_ppu(self):
        if self.ppu is not None:
            if self.mirroring is not None:
                self.ppu.mirroring = self.mirroring
            self.ppu.chr_version += 1


class Mapper0(Mapper):
    """NROM: 16KB (mirrored) or 32KB PRG, fixed CHR."""

    def __init__(self, prg_rom: bytes, chr_rom: bytes, mirroring=None) -> None:
        super().__init__(prg_rom, chr_rom, mirroring)
        self.prg_mask = 0x3FFF if len(prg_rom) <= 16384 else 0x7FFF

    def prg_windows(self):
        if len(self.prg_rom) <= 0x4000:
            return [(0x8000, 0x4000, 0), (0xC000, 0x4000, 0)]   # 16KB mirrored
        return [(0x8000, 0x8000, 0)]

    def read_prg(self, address: int) -> int:
        return self.prg_rom[(address - 0x8000) & self.prg_mask]


class Mapper1(Mapper):
    """MMC1 (SxROM): serial-loaded registers, PRG/CHR banking, mirroring control, 8KB PRG-RAM."""

    MIRROR_FROM_CONTROL = (2, 3, 1, 0)   # one-screen A, one-screen B, vertical, horizontal

    def __init__(self, prg_rom: bytes, chr_rom: bytes, mirroring=None):
        super().__init__(prg_rom, chr_rom, mirroring)
        self.prg_banks_16k = max(len(prg_rom) // 0x4000, 1)
        self.chr_banks_4k = len(chr_rom) // 0x1000
        if not self.has_chr_ram:
            self.chr_window = bytearray(0x2000)     # banked copy of CHR-ROM the PPU reads from
        self.prg_ram = bytearray(0x2000)

        self.shift_register = 0x10   # bit 4 set = empty; the marker bit reaches bit 0 after 4 writes
        self.control = 0x0C          # PRG mode 3 (fix last bank at $C000)
        self.chr_bank_0 = 0
        self.chr_bank_1 = 0
        self.prg_bank = 0
        self._control_written = False

        self.prg_low_offset = 0
        self.prg_high_offset = 0
        self._chr_sig = None
        self.update_banks()

    def write_prg(self, address: int, value: int):
        if value & 0x80:                       # reset
            self.shift_register = 0x10
            self.control |= 0x0C
            self.update_banks()
            return

        complete = (self.shift_register & 1) != 0
        self.shift_register = (self.shift_register >> 1) | ((value & 1) << 4)
        if not complete:
            return

        data = self.shift_register
        self.shift_register = 0x10
        reg = (address >> 13) & 3              # $8000 control, $A000 chr0, $C000 chr1, $E000 prg
        if reg == 0:
            self.control = data
            self._control_written = True
        elif reg == 1:
            self.chr_bank_0 = data
        elif reg == 2:
            self.chr_bank_1 = data
        else:
            self.prg_bank = data & 0x0F
        self.update_banks()

    def update_banks(self):
        self._update_chr()
        self._update_mirroring()
        self._update_prg()

    def _update_prg(self):
        n = self.prg_banks_16k
        mode = (self.control >> 2) & 3
        if mode <= 1:                          # 32KB mode: low bit of the bank number ignored
            bank = self.prg_bank & 0x0E
            low = (bank % n) * 0x4000
            high = ((bank + 1) % n) * 0x4000
        elif mode == 2:                        # first bank fixed at $8000, switch $C000
            low = 0
            high = (self.prg_bank % n) * 0x4000
        else:                                  # last bank fixed at $C000, switch $8000
            low = (self.prg_bank % n) * 0x4000
            high = (n - 1) * 0x4000

        if (low, high) != (self.prg_low_offset, self.prg_high_offset):
            self.prg_low_offset, self.prg_high_offset = low, high
            if self.on_bank_change:
                self.on_bank_change()

    def _update_chr(self):
        if self.has_chr_ram or self.chr_banks_4k == 0:
            return                             # CHR-RAM is not banked (8KB)
        n = self.chr_banks_4k
        if self.control & 0x10:                # two independent 4KB banks
            b0 = self.chr_bank_0 % n
            b1 = self.chr_bank_1 % n
            sig = (1, b0, b1)
            if sig == self._chr_sig:
                return
            self._copy_chr(0x0000, b0 * 0x1000, 0x1000)
            self._copy_chr(0x1000, b1 * 0x1000, 0x1000)
        else:                                  # one 8KB bank (low bit ignored)
            b = (self.chr_bank_0 & 0x1E) % n
            sig = (0, b)
            if sig == self._chr_sig:
                return
            self._copy_chr(0x0000, b * 0x1000, 0x2000)
        self._chr_sig = sig
        self._notify_ppu()

    def _copy_chr(self, dst, src, size):
        data = self.chr_rom[src:src + size]
        self.chr_window[dst:dst + len(data)] = data

    def _update_mirroring(self):
        if self._control_written:
            m = self.MIRROR_FROM_CONTROL[self.control & 3]
            if m != self.mirroring:
                self.mirroring = m
                self._notify_ppu()

    def prg_windows(self):
        return [(0x8000, 0x4000, self.prg_low_offset), (0xC000, 0x4000, self.prg_high_offset)]


MAPPERS = {0: Mapper0, 1: Mapper1}


def make_mapper(rom):
    """Convenience: build the right mapper from a loaded ROM object."""
    cls = MAPPERS.get(rom.mapper_id)
    if cls is None:
        raise NotImplementedError(f"Mapper {rom.mapper_id} not supported yet")
    return cls(rom.prg_rom, rom.chr_rom)