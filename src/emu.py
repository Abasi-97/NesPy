class Emu:
    """Holds the opcode table and the memory interface.
    The CPU creates this and passes itself in, so Emu never imports CPU."""

    def __init__(self, cpu) -> None:
        self.cpu = cpu                      # passed in by CPU
        self.bus = None                     # set by CPU.connect_bus()
        self.pc: int = 0x0000
        self.ram: list[int] = [0] * 65536   # fallback if no bus is attached
        self.opcodes = {}
        self.opcodeSetup()

    def readMem(self, addr: int) -> int:
        addr &= 0xFFFF
        return self.bus.read(addr) if self.bus else self.ram[addr]

    def write_mem(self, addr: int, value: int) -> None:
        addr &= 0xFFFF
        value &= 0xFF
        if self.bus:
            self.bus.write(addr, value)
        else:
            self.ram[addr] = value

    