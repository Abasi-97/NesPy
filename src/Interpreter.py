from emuForInterpreter import Emu

C, Z, I_, D, B, U, V, N = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80


class Interpreter:
    def __init__(self) -> None:
        self.A_reg = 0
        self.X_reg = 0
        self.Y_reg = 0
        self.stack_pointer = 0xFF
        self.statusReg = 0x24
        self.bus = None
        self.last_pc = 0
        self.last_opcode = 0
        self.emu = Emu(self)

    def connect_bus(self, bus):
        self.bus = bus
        self.emu.bus = bus

    def reset(self):
        self.A_reg = self.X_reg = self.Y_reg = 0
        self.stack_pointer = 0xFD
        self.statusReg = 0x24
        low = self.emu.readMem(0xFFFC)
        high = self.emu.readMem(0xFFFD)
        self.emu.pc = (high << 8) | low

    def step(self) -> int:
        self.last_pc = self.emu.pc
        opcode = self.fetchOpcode()
        self.last_opcode = opcode
        entry = self.emu.opcodes.get(opcode)
        if entry is None:
            raise NotImplementedError(
                f"Opcode 0x{opcode:02X} at 0x{(self.emu.pc - 1) & 0xFFFF:04X} is not implemented")
        handler, name, bytes_count, cycles = entry
        handler()
        return cycles

    # ------------------------------------------------------------ memory
    def readMem(self, address: int) -> int:
        return self.emu.readMem(address)

    def write_mem(self, address: int, value: int) -> None:
        self.emu.write_mem(address, value)

    def read_mem16(self, address: int) -> int:
        return self.readMem(address) | (self.readMem(address + 1) << 8)

    def fetchOpcode(self) -> int:
        return self._fetch_byte()

    def _fetch_byte(self) -> int:
        v = self.emu.readMem(self.emu.pc)
        self.emu.pc = (self.emu.pc + 1) & 0xFFFF
        return v

    def fetch_word(self) -> int:
        lo = self._fetch_byte()
        hi = self._fetch_byte()
        return (hi << 8) | lo

    # ------------------------------------------------------------ flags
    def _set_flag(self, mask: int, cond) -> None:
        if cond:
            self.statusReg |= mask
        else:
            self.statusReg &= ~mask

    def update_zero_and_negative_flags(self, result: int):
        result &= 0xFF
        self._set_flag(Z, result == 0)
        self._set_flag(N, result & 0x80)

    # ------------------------------------------------------------ effective addresses
    def _ea_imm(self) -> int:
        a = self.emu.pc
        self.emu.pc = (a + 1) & 0xFFFF
        return a

    def _ea_zp(self) -> int:
        return self._fetch_byte()

    def _ea_zpx(self) -> int:
        return (self._fetch_byte() + self.X_reg) & 0xFF

    def _ea_zpy(self) -> int:
        return (self._fetch_byte() + self.Y_reg) & 0xFF

    def _ea_abs(self) -> int:
        return self.fetch_word()

    def _ea_absx(self) -> int:
        return (self.fetch_word() + self.X_reg) & 0xFFFF

    def _ea_absy(self) -> int:
        return (self.fetch_word() + self.Y_reg) & 0xFFFF

    def _ea_indx(self) -> int:
        ptr = (self._fetch_byte() + self.X_reg) & 0xFF
        lo = self.emu.readMem(ptr)
        hi = self.emu.readMem((ptr + 1) & 0xFF)
        return (hi << 8) | lo

    def _ea_indy(self) -> int:
        zp = self._fetch_byte()
        lo = self.emu.readMem(zp)
        hi = self.emu.readMem((zp + 1) & 0xFF)
        return (((hi << 8) | lo) + self.Y_reg) & 0xFFFF

    # ------------------------------------------------------------ loads
    def _lda(self, v): self.A_reg = v; self.update_zero_and_negative_flags(v)
    def _ldx(self, v): self.X_reg = v; self.update_zero_and_negative_flags(v)
    def _ldy(self, v): self.Y_reg = v; self.update_zero_and_negative_flags(v)

    # ------------------------------------------------------------ logic
    def _and(self, v):
        self.A_reg &= v
        self.update_zero_and_negative_flags(self.A_reg)

    def _ora(self, v):
        self.A_reg |= v
        self.update_zero_and_negative_flags(self.A_reg)

    def _eor(self, v):
        self.A_reg ^= v
        self.update_zero_and_negative_flags(self.A_reg)

    def _do_bit(self, v):
        self._set_flag(Z, (self.A_reg & v) == 0)
        self._set_flag(N, v & 0x80)
        self._set_flag(V, v & 0x40)

    # ------------------------------------------------------------ compare
    def _compare(self, reg, v):
        self._set_flag(C, reg >= v)
        self.update_zero_and_negative_flags((reg - v) & 0xFF)

    def _cmp(self, v): self._compare(self.A_reg, v)
    def _cpx(self, v): self._compare(self.X_reg, v)
    def _cpy(self, v): self._compare(self.Y_reg, v)

    # kept for compatibility with older code
    def do_cmp(self, reg, val): self._compare(reg, val)

    # ------------------------------------------------------------ ADC / SBC (binary + NMOS decimal)
    def _adc_binary(self, val: int):
        a = self.A_reg
        total = a + val + (self.statusReg & C)
        self._set_flag(C, total > 0xFF)
        self._set_flag(V, (~(a ^ val)) & (a ^ total) & 0x80)
        self.A_reg = total & 0xFF
        self.update_zero_and_negative_flags(self.A_reg)

    def do_adc(self, val: int):
        if not (self.statusReg & D):
            self._adc_binary(val)
            return
        a = self.A_reg
        c = self.statusReg & C
        binary = a + val + c
        al = (a & 0x0F) + (val & 0x0F) + c
        if al >= 0x0A:
            al = ((al + 0x06) & 0x0F) + 0x10
        t = (a & 0xF0) + (val & 0xF0) + al
        self._set_flag(N, t & 0x80)
        self._set_flag(V, (~(a ^ val)) & (a ^ t) & 0x80)
        self._set_flag(Z, (binary & 0xFF) == 0)
        if t >= 0xA0:
            t += 0x60
        self._set_flag(C, t >= 0x100)
        self.A_reg = t & 0xFF

    def do_sbc(self, val: int):
        a = self.A_reg
        c = self.statusReg & C
        self._adc_binary(val ^ 0xFF)          # all flags come from the binary result
        if self.statusReg & D:
            al = (a & 0x0F) - (val & 0x0F) + c - 1
            if al < 0:
                al = ((al - 0x06) & 0x0F) - 0x10
            t = (a & 0xF0) - (val & 0xF0) + al
            if t < 0:
                t -= 0x60
            self.A_reg = t & 0xFF

    # ------------------------------------------------------------ shifts / inc / dec (value -> value)
    def _asl(self, v):
        self._set_flag(C, v & 0x80)
        r = (v << 1) & 0xFF
        self.update_zero_and_negative_flags(r)
        return r

    def _lsr(self, v):
        self._set_flag(C, v & 0x01)
        r = v >> 1
        self.update_zero_and_negative_flags(r)
        return r

    def _rol(self, v):
        carry = self.statusReg & C
        self._set_flag(C, v & 0x80)
        r = ((v << 1) & 0xFF) | carry
        self.update_zero_and_negative_flags(r)
        return r

    def _ror(self, v):
        carry = self.statusReg & C
        self._set_flag(C, v & 0x01)
        r = (v >> 1) | (carry << 7)
        self.update_zero_and_negative_flags(r)
        return r

    def _inc(self, v):
        r = (v + 1) & 0xFF
        self.update_zero_and_negative_flags(r)
        return r

    def _dec(self, v):
        r = (v - 1) & 0xFF
        self.update_zero_and_negative_flags(r)
        return r

    def _rmw(self, addr: int, op) -> None:
        self.emu.write_mem(addr, op(self.emu.readMem(addr)))

    def asl_acc(self): self.A_reg = self._asl(self.A_reg)
    def lsr_acc(self): self.A_reg = self._lsr(self.A_reg)
    def rol_acc(self): self.A_reg = self._rol(self.A_reg)
    def ror_acc(self): self.A_reg = self._ror(self.A_reg)

    # ------------------------------------------------------------ register ops
    def tax(self): self.X_reg = self.A_reg; self.update_zero_and_negative_flags(self.X_reg)
    def tay(self): self.Y_reg = self.A_reg; self.update_zero_and_negative_flags(self.Y_reg)
    def txa(self): self.A_reg = self.X_reg; self.update_zero_and_negative_flags(self.A_reg)
    def tya(self): self.A_reg = self.Y_reg; self.update_zero_and_negative_flags(self.A_reg)
    def tsx(self): self.X_reg = self.stack_pointer; self.update_zero_and_negative_flags(self.X_reg)
    def txs(self): self.stack_pointer = self.X_reg

    def inx(self): self.X_reg = (self.X_reg + 1) & 0xFF; self.update_zero_and_negative_flags(self.X_reg)
    def iny(self): self.Y_reg = (self.Y_reg + 1) & 0xFF; self.update_zero_and_negative_flags(self.Y_reg)
    def dex(self): self.X_reg = (self.X_reg - 1) & 0xFF; self.update_zero_and_negative_flags(self.X_reg)
    def dey(self): self.Y_reg = (self.Y_reg - 1) & 0xFF; self.update_zero_and_negative_flags(self.Y_reg)

    def nop(self): pass

    # ------------------------------------------------------------ stack
    def push(self, val: int):
        self.emu.write_mem(0x0100 + self.stack_pointer, val)
        self.stack_pointer = (self.stack_pointer - 1) & 0xFF

    def pop(self) -> int:
        self.stack_pointer = (self.stack_pointer + 1) & 0xFF
        return self.emu.readMem(0x0100 + self.stack_pointer)

    def pha(self): self.push(self.A_reg)

    def pla(self):
        self.A_reg = self.pop()
        self.update_zero_and_negative_flags(self.A_reg)

    def php(self): self.push(self.statusReg | 0x30)

    def plp(self):
        self.statusReg = (self.pop() & ~B) | U

    # ------------------------------------------------------------ branches
    def branch(self, condition: bool):
        offset = self._fetch_byte()
        if condition:
            if offset & 0x80:
                offset -= 256
            self.emu.pc = (self.emu.pc + offset) & 0xFFFF

    def beq(self): self.branch(bool(self.statusReg & Z))
    def bne(self): self.branch(not (self.statusReg & Z))
    def bmi(self): self.branch(bool(self.statusReg & N))
    def bpl(self): self.branch(not (self.statusReg & N))
    def bcs(self): self.branch(bool(self.statusReg & C))
    def bcc(self): self.branch(not (self.statusReg & C))
    def bvs(self): self.branch(bool(self.statusReg & V))
    def bvc(self): self.branch(not (self.statusReg & V))

    # ------------------------------------------------------------ jumps / subroutines
    def jmp_abs(self):
        self.emu.pc = self.fetch_word()

    def jmp_ind(self):
        ptr = self.fetch_word()
        low = self.emu.readMem(ptr)
        # NMOS 6502 bug: high byte fetched without crossing the page
        high = self.emu.readMem((ptr & 0xFF00) | ((ptr + 1) & 0x00FF))
        self.emu.pc = (high << 8) | low

    def jsr(self):
        target = self.fetch_word()
        ret = (self.emu.pc - 1) & 0xFFFF
        self.push(ret >> 8)
        self.push(ret & 0xFF)
        self.emu.pc = target

    def rts(self):
        low = self.pop()
        high = self.pop()
        self.emu.pc = (((high << 8) | low) + 1) & 0xFFFF

    def brk(self):
        self.emu.pc = (self.emu.pc + 1) & 0xFFFF      # skip padding byte
        ret = self.emu.pc
        self.push(ret >> 8)
        self.push(ret & 0xFF)
        self.push(self.statusReg | 0x30)
        self.statusReg |= I_
        low = self.emu.readMem(0xFFFE)
        high = self.emu.readMem(0xFFFF)
        self.emu.pc = (high << 8) | low

    def rti(self):
        self.statusReg = (self.pop() & ~B) | U
        low = self.pop()
        high = self.pop()
        self.emu.pc = (high << 8) | low

    # ------------------------------------------------------------ flag ops
    def clc(self): self.statusReg &= ~C
    def sec(self): self.statusReg |= C
    def cli(self): self.statusReg &= ~I_
    def sei(self): self.statusReg |= I_
    def clv(self): self.statusReg &= ~V
    def cld(self): self.statusReg &= ~D
    def sed(self): self.statusReg |= D