from bus import Bus


class AOT:
    # Status register bit masks
    C = 0x01
    Z = 0x02
    I = 0x04
    D = 0x08
    B = 0x10
    U = 0x20
    V = 0x40
    N = 0x80

    # Standard 6502 Base Cycles Table (Indexed 0x00 to 0xFF)
    CPU_CYCLES = [
        # 0  1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
        7, 6, 2, 8, 3, 3, 5, 5, 3, 2, 2, 2, 4, 4, 6, 6, # 0x00
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6, # 0x10
        6, 6, 2, 8, 3, 3, 5, 5, 4, 2, 2, 2, 4, 4, 6, 6, # 0x20
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6, # 0x30
        6, 6, 2, 8, 3, 3, 5, 5, 3, 2, 2, 2, 3, 4, 6, 6, # 0x40
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6, # 0x50
        6, 6, 2, 8, 3, 3, 5, 5, 4, 2, 2, 2, 5, 4, 6, 6, # 0x60
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6, # 0x70
        2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4, # 0x80
        2, 6, 2, 6, 4, 4, 4, 4, 2, 4, 2, 2, 4, 4, 4, 4, # 0x90
        2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4, # 0xA0
        2, 5, 2, 5, 4, 4, 4, 4, 2, 4, 2, 4, 4, 4, 4, 4, # 0xB0
        2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6, # 0xC0
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6, # 0xD0
        2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6, # 0xE0
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 6, 6  # 0xF0
    ]

    def __init__(self, bus: Bus) -> None:
        self.A_reg = 0
        self.X_reg = 0
        self.Y_reg = 0
        self.PC: int = 0x0000
        self.stack_pointer = 0xFF
        self.statusReg = 0x24  # Unused bit 5 = 1, Interrupt disable = 1
        self.bus = bus

        # Flat 64KB RAM directly in the CPU instance
        self.memory = bytearray(0x10000)

        
        self.last_pc = 0
        self.last_opcode = 0
        self.stack = []
        self.running = True

        # Static dispatch table: opcode byte -> handler method
        self.AOT_REGISTRY = {
            # ADC
            0x69: self.adc_imm, 0x65: self.adc_zp, 0x75: self.adc_zpx, 0x6D: self.adc_abs,
            0x7D: self.adc_absx, 0x79: self.adc_absy, 0x61: self.adc_indx, 0x71: self.adc_indy,
            # AND
            0x29: self.and_imm, 0x25: self.and_zp, 0x35: self.and_zpx, 0x2D: self.and_abs,
            0x3D: self.and_absx, 0x39: self.and_absy, 0x21: self.and_indx, 0x31: self.and_indy,
            # ASL
            0x0A: self.asl_acc, 0x06: self.asl_zp, 0x16: self.asl_zpx, 0x0E: self.asl_abs,
            0x1E: self.asl_absx,
            # Branches
            0x90: self.bcc, 0xB0: self.bcs, 0xF0: self.beq, 0x30: self.bmi,
            0xD0: self.bne, 0x10: self.bpl, 0x50: self.bvc, 0x70: self.bvs,
            # BIT
            0x24: self.bit_zp, 0x2C: self.bit_abs,
            # BRK
            0x00: self.brk,
            # Flag instructions
            0x18: self.clc, 0xD8: self.cld, 0x58: self.cli, 0xB8: self.clv,
            0x38: self.sec, 0xF8: self.sed, 0x78: self.sei,
            # CMP
            0xC9: self.cmp_imm, 0xC5: self.cmp_zp, 0xD5: self.cmp_zpx, 0xCD: self.cmp_abs,
            0xDD: self.cmp_absx, 0xD9: self.cmp_absy, 0xC1: self.cmp_indx, 0xD1: self.cmp_indy,
            # CPX / CPY
            0xE0: self.cpx_imm, 0xE4: self.cpx_zp, 0xEC: self.cpx_abs,
            0xC0: self.cpy_imm, 0xC4: self.cpy_zp, 0xCC: self.cpy_abs,
            # DEC / DEX / DEY
            0xC6: self.dec_zp, 0xD6: self.dec_zpx, 0xCE: self.dec_abs, 0xDE: self.dec_absx,
            0xCA: self.dex, 0x88: self.dey,
            # EOR
            0x49: self.eor_imm, 0x45: self.eor_zp, 0x55: self.eor_zpx, 0x4D: self.eor_abs,
            0x5D: self.eor_absx, 0x59: self.eor_absy, 0x41: self.eor_indx, 0x51: self.eor_indy,
            # INC / INX / INY
            0xE6: self.inc_zp, 0xF6: self.inc_zpx, 0xEE: self.inc_abs, 0xFE: self.inc_absx,
            0xE8: self.inx, 0xC8: self.iny,
            # JMP / JSR
            0x4C: self.jmp_abs, 0x6C: self.jmp_ind, 0x20: self.jsr_abs,
            # LDA
            0xA9: self.lda_imm, 0xA5: self.lda_zp, 0xB5: self.lda_zpx, 0xAD: self.lda_abs,
            0xBD: self.lda_absx, 0xB9: self.lda_absy, 0xA1: self.lda_indx, 0xB1: self.lda_indy,
            # LDX
            0xA2: self.ldx_imm, 0xA6: self.ldx_zp, 0xB6: self.ldx_zpy, 0xAE: self.ldx_abs,
            0xBE: self.ldx_absy,
            # LDY
            0xA0: self.ldy_imm, 0xA4: self.ldy_zp, 0xB4: self.ldy_zpx, 0xAC: self.ldy_abs,
            0xBC: self.ldy_absx,
            # LSR
            0x4A: self.lsr_acc, 0x46: self.lsr_zp, 0x56: self.lsr_zpx, 0x4E: self.lsr_abs,
            0x5E: self.lsr_absx,
            # NOP
            0xEA: self.nop,
            # ORA
            0x09: self.ora_imm, 0x05: self.ora_zp, 0x15: self.ora_zpx, 0x0D: self.ora_abs,
            0x1D: self.ora_absx, 0x19: self.ora_absy, 0x01: self.ora_indx, 0x11: self.ora_indy,
            # Stack
            0x48: self.pha, 0x08: self.php, 0x68: self.pla, 0x28: self.plp,
            # ROL
            0x2A: self.rol_acc, 0x26: self.rol_zp, 0x36: self.rol_zpx, 0x2E: self.rol_abs,
            0x3E: self.rol_absx,
            # ROR
            0x6A: self.ror_acc, 0x66: self.ror_zp, 0x76: self.ror_zpx, 0x6E: self.ror_abs,
            0x7E: self.ror_absx,
            # RTI / RTS
            0x40: self.rti, 0x60: self.rts,
            # SBC
            0xE9: self.sbc_imm, 0xE5: self.sbc_zp, 0xF5: self.sbc_zpx, 0xED: self.sbc_abs,
            0xFD: self.sbc_absx, 0xF9: self.sbc_absy, 0xE1: self.sbc_indx, 0xF1: self.sbc_indy,
            # STA
            0x85: self.sta_zp, 0x95: self.sta_zpx, 0x8D: self.sta_abs, 0x9D: self.sta_absx,
            0x99: self.sta_absy, 0x81: self.sta_indx, 0x91: self.sta_indy,
            # STX / STY
            0x86: self.stx_zp, 0x96: self.stx_zpy, 0x8E: self.stx_abs,
            0x84: self.sty_zp, 0x94: self.sty_zpx, 0x8C: self.sty_abs,
            # Transfers
            0xAA: self.tax, 0xA8: self.tay, 0xBA: self.tsx,
            0x8A: self.txa, 0x9A: self.txs, 0x98: self.tya,
        }

    # ==========================================
    # Memory access helpers
    # ==========================================
    def reset(self):
        # 1. Point the uppercase self.PC to the Reset Vector addresses ($FFFC - $FFFD)
        self.PC = 0xFFFC
        
        # 2. Fetch the low and high bytes through the Bus
        lo = self.read_byte(self.PC)
        self.PC += 1
        hi = self.read_byte(self.PC)
        self.PC += 1
        
        # 3. Combine bytes into the 16-bit start address
        self.PC = (hi << 8) | lo
        
        print(f"[CPU] Reset complete. Program Counter initialized to: 0x{self.PC:04X}")

    def fetch_byte(self) -> int:
        # Route through self.read_byte so it queries the Bus -> Mapper -> ROM!
        val = self.read_byte(self.PC)
        self.PC = (self.PC + 1) & 0xFFFF
        return val

    def read_byte(self, address: int) -> int:
        address &= 0xFFFF
        # Fast-path: Direct RAM array access (bypasses bus method/overhead entirely!)
        if address < 0x2000:
            return self.bus.ram[address & 0x7FF]
        # Fallback to bus page table for PPU, IO, and ROM
        return self.bus.read(address)

    def write_byte(self, address: int, value: int):
        address &= 0xFFFF
        value &= 0xFF
        # Fast-path: Direct RAM array write
        if address < 0x2000:
            self.bus.ram[address & 0x7FF] = value
            return
        # Fallback to bus page table for PPU, IO, and ROM mapper writes
        self.bus.write(address, value)

    def fetch_word(self) -> int:
        lo = self.fetch_byte()
        hi = self.fetch_byte()
        return (hi << 8) | lo

    def fetchOpcode(self) -> int:
        return self.fetch_byte()

    def push_byte(self, value: int) -> None:
        self.memory[0x0100 + self.stack_pointer] = value & 0xFF
        self.stack_pointer = (self.stack_pointer - 1) & 0xFF

    def pull_byte(self) -> int:
        self.stack_pointer = (self.stack_pointer + 1) & 0xFF
        return self.memory[0x0100 + self.stack_pointer]

    def update_zero_and_negative_flags(self, value: int) -> None:
        value &= 0xFF
        if value == 0:
            self.statusReg |= 0x02
        else:
            self.statusReg &= ~0x02
        if value & 0x80:
            self.statusReg |= 0x80
        else:
            self.statusReg &= ~0x80

    # ==========================================
    # Addressing mode helpers
    # ==========================================
    def addr_immediate(self) -> int:
        addr = self.PC
        self.PC = (self.PC + 1) & 0xFFFF
        return addr

    def addr_zero_page(self) -> int:
        return self.fetch_byte()

    def addr_zero_page_x(self) -> int:
        return (self.fetch_byte() + self.X_reg) & 0xFF

    def addr_zero_page_y(self) -> int:
        return (self.fetch_byte() + self.Y_reg) & 0xFF

    def addr_absolute(self) -> int:
        return self.fetch_word()

    def addr_absolute_x(self) -> int:
        return (self.fetch_word() + self.X_reg) & 0xFFFF

    def addr_absolute_y(self) -> int:
        return (self.fetch_word() + self.Y_reg) & 0xFFFF

    def addr_indirect_x(self) -> int:
        ptr = (self.fetch_byte() + self.X_reg) & 0xFF
        lo = self.memory[ptr]
        hi = self.memory[(ptr + 1) & 0xFF]
        return (hi << 8) | lo

    def addr_indirect_y(self) -> int:
        ptr = self.fetch_byte()
        lo = self.memory[ptr]
        hi = self.memory[(ptr + 1) & 0xFF]
        return (((hi << 8) | lo) + self.Y_reg) & 0xFFFF

    def branch_relative(self, condition: bool) -> None:
        offset = self.fetch_byte()
        if condition:
            if offset & 0x80:          # FIX: sign-extend when the high bit is SET
                offset -= 0x100
            self.PC = (self.PC + offset) & 0xFFFF

    # ==========================================
    # ADC / SBC (with NMOS 6502 decimal mode)
    # ==========================================
    def adc(self, value: int) -> None:
        a = self.A_reg
        carry = self.statusReg & 0x01
        binary = a + value + carry

        if self.statusReg & 0x08:  # decimal mode
            # Z comes from the binary result on NMOS
            if (binary & 0xFF) == 0:
                self.statusReg |= 0x02
            else:
                self.statusReg &= ~0x02

            al = (a & 0x0F) + (value & 0x0F) + carry
            if al >= 0x0A:
                al = ((al + 0x06) & 0x0F) + 0x10
            tmp = (a & 0xF0) + (value & 0xF0) + al

            # N and V come from the intermediate value (before high-nibble fix)
            if tmp & 0x80:
                self.statusReg |= 0x80
            else:
                self.statusReg &= ~0x80
            if ((a ^ tmp) & (value ^ tmp) & 0x80):
                self.statusReg |= 0x40
            else:
                self.statusReg &= ~0x40

            if tmp >= 0xA0:
                tmp += 0x60
            if tmp >= 0x100:
                self.statusReg |= 0x01
            else:
                self.statusReg &= ~0x01
            self.A_reg = tmp & 0xFF
            return

        if binary > 0xFF:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        result = binary & 0xFF
        if ((a ^ result) & (value ^ result) & 0x80):
            self.statusReg |= 0x40
        else:
            self.statusReg &= ~0x40
        self.A_reg = result
        self.update_zero_and_negative_flags(result)

    def sbc(self, value: int) -> None:
        a = self.A_reg
        carry = self.statusReg & 0x01
        binary = a - value - (1 - carry)
        result = binary & 0xFF

        # Flags are computed from the binary result in both modes on NMOS
        if binary >= 0:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        if ((a ^ value) & (a ^ result) & 0x80):
            self.statusReg |= 0x40
        else:
            self.statusReg &= ~0x40
        self.update_zero_and_negative_flags(result)

        if self.statusReg & 0x08:  # decimal mode: only the accumulator differs
            al = (a & 0x0F) - (value & 0x0F) + carry - 1
            if al < 0:
                al = ((al - 0x06) & 0x0F) - 0x10
            tmp = (a & 0xF0) - (value & 0xF0) + al
            if tmp < 0:
                tmp -= 0x60
            self.A_reg = tmp & 0xFF
        else:
            self.A_reg = result

    def adc_imm(self): self.adc(self.read_byte(self.addr_immediate()))
    def adc_zp(self): self.adc(self.read_byte(self.addr_zero_page()))
    def adc_zpx(self): self.adc(self.read_byte(self.addr_zero_page_x()))
    def adc_abs(self): self.adc(self.read_byte(self.addr_absolute()))
    def adc_absx(self): self.adc(self.read_byte(self.addr_absolute_x()))
    def adc_absy(self): self.adc(self.read_byte(self.addr_absolute_y()))
    def adc_indx(self): self.adc(self.read_byte(self.addr_indirect_x()))
    def adc_indy(self): self.adc(self.read_byte(self.addr_indirect_y()))

    def sbc_imm(self): self.sbc(self.read_byte(self.addr_immediate()))
    def sbc_zp(self): self.sbc(self.read_byte(self.addr_zero_page()))
    def sbc_zpx(self): self.sbc(self.read_byte(self.addr_zero_page_x()))
    def sbc_abs(self): self.sbc(self.read_byte(self.addr_absolute()))
    def sbc_absx(self): self.sbc(self.read_byte(self.addr_absolute_x()))
    def sbc_absy(self): self.sbc(self.read_byte(self.addr_absolute_y()))
    def sbc_indx(self): self.sbc(self.read_byte(self.addr_indirect_x()))
    def sbc_indy(self): self.sbc(self.read_byte(self.addr_indirect_y()))

    # --- AND ---
    def logical_AND(self, value: int) -> None:
        self.A_reg &= value
        self.update_zero_and_negative_flags(self.A_reg)

    def and_imm(self): self.logical_AND(self.read_byte(self.addr_immediate()))
    def and_zp(self): self.logical_AND(self.read_byte(self.addr_zero_page()))
    def and_zpx(self): self.logical_AND(self.read_byte(self.addr_zero_page_x()))
    def and_abs(self): self.logical_AND(self.read_byte(self.addr_absolute()))
    def and_absx(self): self.logical_AND(self.read_byte(self.addr_absolute_x()))
    def and_absy(self): self.logical_AND(self.read_byte(self.addr_absolute_y()))
    def and_indx(self): self.logical_AND(self.read_byte(self.addr_indirect_x()))
    def and_indy(self): self.logical_AND(self.read_byte(self.addr_indirect_y()))

    # --- ASL ---
    def arithmetic_shift_left(self, value: int) -> int:
        if value & 0x80:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        result = (value << 1) & 0xFF
        self.update_zero_and_negative_flags(result)
        return result

    def asl_acc(self): self.A_reg = self.arithmetic_shift_left(self.A_reg)
    def asl_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.arithmetic_shift_left(self.read_byte(addr)))
    def asl_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.arithmetic_shift_left(self.read_byte(addr)))
    def asl_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.arithmetic_shift_left(self.read_byte(addr)))
    def asl_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.arithmetic_shift_left(self.read_byte(addr)))

    # --- Branches ---
    def bcc(self): self.branch_relative((self.statusReg & 0x01) == 0)
    def bcs(self): self.branch_relative((self.statusReg & 0x01) != 0)
    def beq(self): self.branch_relative((self.statusReg & 0x02) != 0)
    def bmi(self): self.branch_relative((self.statusReg & 0x80) != 0)
    def bne(self): self.branch_relative((self.statusReg & 0x02) == 0)
    def bpl(self): self.branch_relative((self.statusReg & 0x80) == 0)
    def bvc(self): self.branch_relative((self.statusReg & 0x40) == 0)
    def bvs(self): self.branch_relative((self.statusReg & 0x40) != 0)

    # --- BIT ---
    def bit(self, value: int) -> None:
        if (self.A_reg & value) == 0:
            self.statusReg |= 0x02
        else:
            self.statusReg &= ~0x02
        self.statusReg = (self.statusReg & 0x3F) | (value & 0xC0)

    def bit_zp(self): self.bit(self.read_byte(self.addr_zero_page()))
    def bit_abs(self): self.bit(self.read_byte(self.addr_absolute()))

    # --- BRK ---
    def brk(self):
        # BRK has a padding byte: the pushed return address is BRK + 2
        self.PC = (self.PC + 1) & 0xFFFF
        self.push_byte(self.PC >> 8)
        self.push_byte(self.PC & 0xFF)
        self.push_byte(self.statusReg | 0x30)   # B and unused set in pushed copy
        self.statusReg |= 0x04                  # set I
        lo = self.read_byte(0xFFFE)
        hi = self.read_byte(0xFFFF)
        self.PC = (hi << 8) | lo
        # FIX: no longer stops the CPU; the Klaus test exercises BRK/RTI

    # --- Flag instructions ---
    def clc(self): self.statusReg &= ~0x01
    def cld(self): self.statusReg &= ~0x08
    def cli(self): self.statusReg &= ~0x04
    def clv(self): self.statusReg &= ~0x40
    def sec(self): self.statusReg |= 0x01
    def sed(self): self.statusReg |= 0x08
    def sei(self): self.statusReg |= 0x04

    # --- CMP / CPX / CPY ---
    def cmp_val(self, reg_val: int, value: int) -> None:
        result = (reg_val - value) & 0xFF
        if reg_val >= value:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        self.update_zero_and_negative_flags(result)

    def cmp(self, value: int): self.cmp_val(self.A_reg, value)
    def cmp_imm(self): self.cmp(self.read_byte(self.addr_immediate()))
    def cmp_zp(self): self.cmp(self.read_byte(self.addr_zero_page()))
    def cmp_zpx(self): self.cmp(self.read_byte(self.addr_zero_page_x()))
    def cmp_abs(self): self.cmp(self.read_byte(self.addr_absolute()))
    def cmp_absx(self): self.cmp(self.read_byte(self.addr_absolute_x()))
    def cmp_absy(self): self.cmp(self.read_byte(self.addr_absolute_y()))
    def cmp_indx(self): self.cmp(self.read_byte(self.addr_indirect_x()))
    def cmp_indy(self): self.cmp(self.read_byte(self.addr_indirect_y()))

    def cpx(self, value: int): self.cmp_val(self.X_reg, value)
    def cpx_imm(self): self.cpx(self.read_byte(self.addr_immediate()))
    def cpx_zp(self): self.cpx(self.read_byte(self.addr_zero_page()))
    def cpx_abs(self): self.cpx(self.read_byte(self.addr_absolute()))

    def cpy(self, value: int): self.cmp_val(self.Y_reg, value)
    def cpy_imm(self): self.cpy(self.read_byte(self.addr_immediate()))
    def cpy_zp(self): self.cpy(self.read_byte(self.addr_zero_page()))
    def cpy_abs(self): self.cpy(self.read_byte(self.addr_absolute()))

    # --- INC / DEC ---
    def decrement_value(self, value: int) -> int:
        result = (value - 1) & 0xFF
        self.update_zero_and_negative_flags(result)
        return result

    def dec_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.decrement_value(self.read_byte(addr)))
    def dec_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.decrement_value(self.read_byte(addr)))
    def dec_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.decrement_value(self.read_byte(addr)))
    def dec_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.decrement_value(self.read_byte(addr)))

    def dex(self):
        self.X_reg = (self.X_reg - 1) & 0xFF
        self.update_zero_and_negative_flags(self.X_reg)

    def dey(self):
        self.Y_reg = (self.Y_reg - 1) & 0xFF
        self.update_zero_and_negative_flags(self.Y_reg)

    def increment_value(self, value: int) -> int:
        result = (value + 1) & 0xFF
        self.update_zero_and_negative_flags(result)
        return result

    def inc_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.increment_value(self.read_byte(addr)))
    def inc_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.increment_value(self.read_byte(addr)))
    def inc_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.increment_value(self.read_byte(addr)))
    def inc_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.increment_value(self.read_byte(addr)))

    def inx(self):
        self.X_reg = (self.X_reg + 1) & 0xFF
        self.update_zero_and_negative_flags(self.X_reg)

    def iny(self):
        self.Y_reg = (self.Y_reg + 1) & 0xFF
        self.update_zero_and_negative_flags(self.Y_reg)

    # --- EOR ---
    def eor(self, value: int) -> None:
        self.A_reg = (self.A_reg ^ value) & 0xFF
        self.update_zero_and_negative_flags(self.A_reg)

    def eor_imm(self): self.eor(self.read_byte(self.addr_immediate()))
    def eor_zp(self): self.eor(self.read_byte(self.addr_zero_page()))
    def eor_zpx(self): self.eor(self.read_byte(self.addr_zero_page_x()))
    def eor_abs(self): self.eor(self.read_byte(self.addr_absolute()))
    def eor_absx(self): self.eor(self.read_byte(self.addr_absolute_x()))
    def eor_absy(self): self.eor(self.read_byte(self.addr_absolute_y()))
    def eor_indx(self): self.eor(self.read_byte(self.addr_indirect_x()))
    def eor_indy(self): self.eor(self.read_byte(self.addr_indirect_y()))

    # --- JMP / JSR ---
    def jmp_abs(self):
        self.PC = self.addr_absolute()

    def jmp_ind(self):
        ptr = self.fetch_word()
        lo = self.read_byte(ptr)
        # 6502 page-boundary bug: high byte wraps within the same page
        hi = self.read_byte((ptr & 0xFF00) | ((ptr + 1) & 0x00FF))
        self.PC = (hi << 8) | lo

    def jsr_abs(self):
        target_address = self.addr_absolute()
        return_address = (self.PC - 1) & 0xFFFF
        self.push_byte(return_address >> 8)
        self.push_byte(return_address & 0xFF)
        self.PC = target_address

    # --- LDA / LDX / LDY ---
    def lda(self, value: int):
        self.A_reg = value & 0xFF
        self.update_zero_and_negative_flags(self.A_reg)

    def lda_imm(self): self.lda(self.read_byte(self.addr_immediate()))
    def lda_zp(self): self.lda(self.read_byte(self.addr_zero_page()))
    def lda_zpx(self): self.lda(self.read_byte(self.addr_zero_page_x()))
    def lda_abs(self): self.lda(self.read_byte(self.addr_absolute()))
    def lda_absx(self): self.lda(self.read_byte(self.addr_absolute_x()))
    def lda_absy(self): self.lda(self.read_byte(self.addr_absolute_y()))
    def lda_indx(self): self.lda(self.read_byte(self.addr_indirect_x()))
    def lda_indy(self): self.lda(self.read_byte(self.addr_indirect_y()))

    def ldx(self, value: int):
        self.X_reg = value & 0xFF
        self.update_zero_and_negative_flags(self.X_reg)

    def ldx_imm(self): self.ldx(self.read_byte(self.addr_immediate()))
    def ldx_zp(self): self.ldx(self.read_byte(self.addr_zero_page()))
    def ldx_zpy(self): self.ldx(self.read_byte(self.addr_zero_page_y()))
    def ldx_abs(self): self.ldx(self.read_byte(self.addr_absolute()))
    def ldx_absy(self): self.ldx(self.read_byte(self.addr_absolute_y()))

    def ldy(self, value: int):
        self.Y_reg = value & 0xFF
        self.update_zero_and_negative_flags(self.Y_reg)

    def ldy_imm(self): self.ldy(self.read_byte(self.addr_immediate()))
    def ldy_zp(self): self.ldy(self.read_byte(self.addr_zero_page()))
    def ldy_zpx(self): self.ldy(self.read_byte(self.addr_zero_page_x()))
    def ldy_abs(self): self.ldy(self.read_byte(self.addr_absolute()))
    def ldy_absx(self): self.ldy(self.read_byte(self.addr_absolute_x()))

    # --- STA / STX / STY ---
    def sta(self, addr: int): self.write_byte(addr, self.A_reg)
    def sta_zp(self): self.sta(self.addr_zero_page())
    def sta_zpx(self): self.sta(self.addr_zero_page_x())
    def sta_abs(self): self.sta(self.addr_absolute())
    def sta_absx(self): self.sta(self.addr_absolute_x())
    def sta_absy(self): self.sta(self.addr_absolute_y())
    def sta_indx(self): self.sta(self.addr_indirect_x())
    def sta_indy(self): self.sta(self.addr_indirect_y())

    def stx(self, addr: int): self.write_byte(addr, self.X_reg)
    def stx_zp(self): self.stx(self.addr_zero_page())
    def stx_zpy(self): self.stx(self.addr_zero_page_y())
    def stx_abs(self): self.stx(self.addr_absolute())

    def sty(self, addr: int): self.write_byte(addr, self.Y_reg)
    def sty_zp(self): self.sty(self.addr_zero_page())
    def sty_zpx(self): self.sty(self.addr_zero_page_x())
    def sty_abs(self): self.sty(self.addr_absolute())

    # --- LSR ---
    def lsr(self, value: int) -> int:
        if value & 0x01:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        result = (value >> 1) & 0xFF
        self.update_zero_and_negative_flags(result)
        return result

    def lsr_acc(self): self.A_reg = self.lsr(self.A_reg)
    def lsr_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.lsr(self.read_byte(addr)))
    def lsr_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.lsr(self.read_byte(addr)))
    def lsr_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.lsr(self.read_byte(addr)))
    def lsr_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.lsr(self.read_byte(addr)))

    # --- NOP ---
    def nop(self): pass

    # --- ORA ---
    def ora(self, value: int):
        self.A_reg = (self.A_reg | value) & 0xFF
        self.update_zero_and_negative_flags(self.A_reg)

    def ora_imm(self): self.ora(self.read_byte(self.addr_immediate()))
    def ora_zp(self): self.ora(self.read_byte(self.addr_zero_page()))
    def ora_zpx(self): self.ora(self.read_byte(self.addr_zero_page_x()))
    def ora_abs(self): self.ora(self.read_byte(self.addr_absolute()))
    def ora_absx(self): self.ora(self.read_byte(self.addr_absolute_x()))
    def ora_absy(self): self.ora(self.read_byte(self.addr_absolute_y()))
    def ora_indx(self): self.ora(self.read_byte(self.addr_indirect_x()))
    def ora_indy(self): self.ora(self.read_byte(self.addr_indirect_y()))

    # --- Stack operations ---
    def pha(self):
        self.push_byte(self.A_reg)

    def php(self):
        self.push_byte(self.statusReg | 0x30)

    def pla(self):
        self.A_reg = self.pull_byte()
        self.update_zero_and_negative_flags(self.A_reg)

    def plp(self):
        val = self.pull_byte()
        # Bit 4 (B) doesn't exist in the register; bit 5 always reads 1.
        self.statusReg = (val & 0xCF) | 0x20   # FIX: debug print removed

    # --- ROL / ROR ---
    def rol(self, value: int) -> int:
        old_carry = self.statusReg & 0x01
        result = ((value << 1) & 0xFF) | old_carry
        if value & 0x80:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        self.update_zero_and_negative_flags(result)
        return result

    def rol_acc(self): self.A_reg = self.rol(self.A_reg)
    def rol_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.rol(self.read_byte(addr)))
    def rol_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.rol(self.read_byte(addr)))
    def rol_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.rol(self.read_byte(addr)))
    def rol_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.rol(self.read_byte(addr)))

    def ror(self, value: int) -> int:
        old_carry = (self.statusReg & 0x01) << 7
        result = (value >> 1) | old_carry
        if value & 0x01:
            self.statusReg |= 0x01
        else:
            self.statusReg &= ~0x01
        self.update_zero_and_negative_flags(result)
        return result

    def ror_acc(self): self.A_reg = self.ror(self.A_reg)
    def ror_zp(self):
        addr = self.addr_zero_page()
        self.write_byte(addr, self.ror(self.read_byte(addr)))
    def ror_zpx(self):
        addr = self.addr_zero_page_x()
        self.write_byte(addr, self.ror(self.read_byte(addr)))
    def ror_abs(self):
        addr = self.addr_absolute()
        self.write_byte(addr, self.ror(self.read_byte(addr)))
    def ror_absx(self):
        addr = self.addr_absolute_x()
        self.write_byte(addr, self.ror(self.read_byte(addr)))

    # --- RTI / RTS ---
    def rti(self):
        self.plp()
        pc_low = self.pull_byte()
        pc_high = self.pull_byte()
        self.PC = (pc_high << 8) | pc_low        # RTI does NOT add 1

    def rts(self):
        pc_low = self.pull_byte()
        pc_high = self.pull_byte()
        self.PC = (((pc_high << 8) | pc_low) + 1) & 0xFFFF   # RTS adds 1

    # --- Register transfers ---
    def tax(self):
        self.X_reg = self.A_reg
        self.update_zero_and_negative_flags(self.X_reg)

    def tay(self):
        self.Y_reg = self.A_reg
        self.update_zero_and_negative_flags(self.Y_reg)

    def tsx(self):
        self.X_reg = self.stack_pointer
        self.update_zero_and_negative_flags(self.X_reg)

    def txa(self):
        self.A_reg = self.X_reg
        self.update_zero_and_negative_flags(self.A_reg)

    def txs(self):
        self.stack_pointer = self.X_reg   # TXS affects no flags

    def tya(self):
        self.A_reg = self.Y_reg
        self.update_zero_and_negative_flags(self.A_reg)

    # ==========================================
    # Execution
    # ==========================================
    def step(self) -> int:
        self.last_pc = self.PC
        opcode = self.fetch_byte()
        self.last_opcode = opcode

        # Look up the cycle cost of this instruction
        cycles = self.CPU_CYCLES[opcode]

        handler = self.AOT_REGISTRY.get(opcode)
        if handler:
            handler()
        else:
            self.running = False
            print(f"Could not find opcode: {hex(opcode)} at PC: {hex(self.last_pc)}")
            
        # Notify the bus/PPU how many cycles just elapsed
        if self.bus and hasattr(self.bus, "tick"):
            self.bus.tick(cycles)
            
        return cycles

    def emulationLoop(self):
        while self.running:
            self.step()
    
    