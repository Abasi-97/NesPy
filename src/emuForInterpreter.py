class Emu:
    def __init__(self, interpreter) -> None:
        self.interpreter = interpreter  # back-reference to the Interpreter
        self.pc: int = 0x000
        self.ram: list[int] = [0] * 65536
        self.A_reg: int = 0
        self.X_reg: int = 0
        self.Y_reg: int = 0
        self.stack_pointer: int = 0xFF
        self.statusReg = 0x24
        self.opcodes = {}
        self.opcodeSetup()

    def readMem(self, address: int) -> int:
        return self.ram[address & 0xFFFF]

    def write_mem(self, address: int, value: int) -> None:
        self.ram[address & 0xFFFF] = value & 0xFF

    def opcodeSetup(self):
        I = self.interpreter
        op = self.opcodes

        # mode name -> (effective-address function, instruction bytes, base cycles)
        modes = {
            "imm":  (I._ea_imm,  2, 2),
            "zp":   (I._ea_zp,   2, 3),
            "zpx":  (I._ea_zpx,  2, 4),
            "zpy":  (I._ea_zpy,  2, 4),
            "abs":  (I._ea_abs,  3, 4),
            "absx": (I._ea_absx, 3, 4),
            "absy": (I._ea_absy, 3, 4),
            "indx": (I._ea_indx, 2, 6),
            "indy": (I._ea_indy, 2, 5),
        }
        pretty = {
            "imm": "Immediate", "zp": "Zero Page", "zpx": "Zero Page,X",
            "zpy": "Zero Page,Y", "abs": "Absolute", "absx": "Absolute,X",
            "absy": "Absolute,Y", "indx": "(Indirect,X)", "indy": "(Indirect),Y",
        }

        def make_read(fn, ea):
            def h():
                fn(self.ram[ea() & 0xFFFF])
            return h

        def make_store(reg_name, ea):
            def h():
                self.ram[ea() & 0xFFFF] = getattr(I, reg_name) & 0xFF
            return h

        def make_rmw(fn, ea):
            def h():
                I._rmw(ea(), fn)
            return h

        # --- read instructions: mnemonic -> (handler, {mode: opcode}) ---
        reads = {
            "LDA": (I._lda, dict(imm=0xA9, zp=0xA5, zpx=0xB5, abs=0xAD, absx=0xBD, absy=0xB9, indx=0xA1, indy=0xB1)),
            "LDX": (I._ldx, dict(imm=0xA2, zp=0xA6, zpy=0xB6, abs=0xAE, absy=0xBE)),
            "LDY": (I._ldy, dict(imm=0xA0, zp=0xA4, zpx=0xB4, abs=0xAC, absx=0xBC)),
            "ADC": (I.do_adc, dict(imm=0x69, zp=0x65, zpx=0x75, abs=0x6D, absx=0x7D, absy=0x79, indx=0x61, indy=0x71)),
            "SBC": (I.do_sbc, dict(imm=0xE9, zp=0xE5, zpx=0xF5, abs=0xED, absx=0xFD, absy=0xF9, indx=0xE1, indy=0xF1)),
            "AND": (I._and, dict(imm=0x29, zp=0x25, zpx=0x35, abs=0x2D, absx=0x3D, absy=0x39, indx=0x21, indy=0x31)),
            "ORA": (I._ora, dict(imm=0x09, zp=0x05, zpx=0x15, abs=0x0D, absx=0x1D, absy=0x19, indx=0x01, indy=0x11)),
            "EOR": (I._eor, dict(imm=0x49, zp=0x45, zpx=0x55, abs=0x4D, absx=0x5D, absy=0x59, indx=0x41, indy=0x51)),
            "CMP": (I._cmp, dict(imm=0xC9, zp=0xC5, zpx=0xD5, abs=0xCD, absx=0xDD, absy=0xD9, indx=0xC1, indy=0xD1)),
            "CPX": (I._cpx, dict(imm=0xE0, zp=0xE4, abs=0xEC)),
            "CPY": (I._cpy, dict(imm=0xC0, zp=0xC4, abs=0xCC)),
            "BIT": (I._do_bit, dict(zp=0x24, abs=0x2C)),
        }
        for name, (fn, table) in reads.items():
            for mode, code in table.items():
                ea, nbytes, cycles = modes[mode]
                op[code] = (make_read(fn, ea), f"{name} {pretty[mode]}", nbytes, cycles)

        # --- stores (indexed modes take one extra cycle) ---
        stores = {
            "STA": ("A_reg", dict(zp=0x85, zpx=0x95, abs=0x8D, absx=0x9D, absy=0x99, indx=0x81, indy=0x91)),
            "STX": ("X_reg", dict(zp=0x86, zpy=0x96, abs=0x8E)),
            "STY": ("Y_reg", dict(zp=0x84, zpx=0x94, abs=0x8C)),
        }
        store_cycles = {"zp": 3, "zpx": 4, "zpy": 4, "abs": 4, "absx": 5, "absy": 5, "indx": 6, "indy": 6}
        for name, (reg, table) in stores.items():
            for mode, code in table.items():
                ea, nbytes, _ = modes[mode]
                op[code] = (make_store(reg, ea), f"{name} {pretty[mode]}", nbytes, store_cycles[mode])

        # --- read-modify-write ---
        rmws = {
            "ASL": (I._asl, dict(zp=0x06, zpx=0x16, abs=0x0E, absx=0x1E)),
            "LSR": (I._lsr, dict(zp=0x46, zpx=0x56, abs=0x4E, absx=0x5E)),
            "ROL": (I._rol, dict(zp=0x26, zpx=0x36, abs=0x2E, absx=0x3E)),
            "ROR": (I._ror, dict(zp=0x66, zpx=0x76, abs=0x6E, absx=0x7E)),
            "INC": (I._inc, dict(zp=0xE6, zpx=0xF6, abs=0xEE, absx=0xFE)),
            "DEC": (I._dec, dict(zp=0xC6, zpx=0xD6, abs=0xCE, absx=0xDE)),
        }
        rmw_cycles = {"zp": 5, "zpx": 6, "abs": 6, "absx": 7}
        for name, (fn, table) in rmws.items():
            for mode, code in table.items():
                ea, nbytes, _ = modes[mode]
                op[code] = (make_rmw(fn, ea), f"{name} {pretty[mode]}", nbytes, rmw_cycles[mode])

        # --- accumulator shifts / rotates ---
        op[0x0A] = (I.asl_acc, "ASL A", 1, 2)
        op[0x4A] = (I.lsr_acc, "LSR A", 1, 2)
        op[0x2A] = (I.rol_acc, "ROL A", 1, 2)
        op[0x6A] = (I.ror_acc, "ROR A", 1, 2)

        # --- implied / single-byte ---
        implied = [
            (0xAA, "TAX", I.tax, 2), (0xA8, "TAY", I.tay, 2),
            (0x8A, "TXA", I.txa, 2), (0x98, "TYA", I.tya, 2),
            (0xBA, "TSX", I.tsx, 2), (0x9A, "TXS", I.txs, 2),
            (0xE8, "INX", I.inx, 2), (0xC8, "INY", I.iny, 2),
            (0xCA, "DEX", I.dex, 2), (0x88, "DEY", I.dey, 2),
            (0x48, "PHA", I.pha, 3), (0x68, "PLA", I.pla, 4),
            (0x08, "PHP", I.php, 3), (0x28, "PLP", I.plp, 4),
            (0x18, "CLC", I.clc, 2), (0x38, "SEC", I.sec, 2),
            (0x58, "CLI", I.cli, 2), (0x78, "SEI", I.sei, 2),
            (0xB8, "CLV", I.clv, 2), (0xD8, "CLD", I.cld, 2),
            (0xF8, "SED", I.sed, 2), (0xEA, "NOP", I.nop, 2),
            (0x40, "RTI", I.rti, 6), (0x60, "RTS", I.rts, 6),
        ]
        for code, name, fn, cycles in implied:
            op[code] = (fn, name, 1, cycles)
        op[0x00] = (I.brk, "BRK", 2, 7)

        # --- jumps ---
        op[0x4C] = (I.jmp_abs, "JMP Absolute", 3, 3)
        op[0x6C] = (I.jmp_ind, "JMP Indirect", 3, 5)
        op[0x20] = (I.jsr,     "JSR Absolute", 3, 6)

        # --- branches ---
        for code, name, fn in [
            (0xF0, "BEQ", I.beq), (0xD0, "BNE", I.bne),
            (0x30, "BMI", I.bmi), (0x10, "BPL", I.bpl),
            (0xB0, "BCS", I.bcs), (0x90, "BCC", I.bcc),
            (0x50, "BVC", I.bvc), (0x70, "BVS", I.bvs),
        ]:
            op[code] = (fn, f"{name} Relative", 2, 2)