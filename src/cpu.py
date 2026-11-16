from bus import Bus

class CPU:
    def __init__(self, bus: Bus) -> None:
        self.A_reg = 0
        self.X_reg = 0
        self.Y_reg = 0
        self.PC: int = 0x0000
        self.stack_pointer = 0xFF
        self.statusReg = 0x24  # Unused bit 5 = 1, Interrupt disable = 1
        self.bus = bus
        # Execution state and flags for JIT templates
        self.cycles = 0
        self.C_flag = False
        self.Z_flag = False
        self.I_flag = True
        self.D_flag = False
        self.B_flag = False
        self.V_flag = False
        self.N_flag = False

        self.last_pc = 0
        self.last_opcode = 0
        self.stack = []
        self.running = True
        self.jit = None
        self.code_pages = bytearray(256)   # 1 = some compiled block was built from this page
        self.ram = bytearray(2048)         # replaced by the Bus's RAM when the Bus is created

    # Status register bit masks
    C = 0x01
    Z = 0x02
    I = 0x04
    D = 0x08
    B = 0x10
    U = 0x20
    V = 0x40
    N = 0x80

    def read_byte(self, address: int) -> int:
        if address < 0x2000:
            return self.ram[address & 0x7FF]    # internal RAM + mirrors, no bus call
        return self.bus.read(address)

    def write_byte(self, address: int, value: int) -> None:
        address &= 0xFFFF
        if address < 0x2000:
            a = address & 0x7FF                 # RAM mirrors: $0800 apart is the same byte
            value &= 0xFF
            if self.code_pages[a >> 8] and self.ram[a] != value:
                self.jit.invalidate(a)          # self-modifying code
            self.ram[a] = value
            return
        page = address >> 8
        if self.code_pages[page] and self.bus.read(address) != (value & 0xFF):
            self.jit.invalidate(address)
        self.bus.write(address, value)

    def get_status(self, b_flag=False) -> int:
        return ((self.N_flag << 7) | (self.V_flag << 6) | 0x20 | (int(b_flag) << 4) |
                (self.D_flag << 3) | (self.I_flag << 2) | (self.Z_flag << 1) | int(self.C_flag))

    def read_word(self, address: int) -> int:
        lo = self.read_byte(address)
        hi = self.read_byte(address + 1)
        return (hi << 8) | lo

    def push_stack(self, val: int) -> None:
        self.write_byte(0x0100 + self.stack_pointer, val)
        self.stack_pointer = (self.stack_pointer - 1) & 0xFF

    def pop_stack(self) -> int:
        self.stack_pointer = (self.stack_pointer + 1) & 0xFF
        return self.read_byte(0x0100 + self.stack_pointer)

    # Standard 6502 Base Cycles Table (Indexed 0x00 to 0xFF)
    CPU_CYCLES = [
        7, 6, 2, 8, 3, 3, 5, 5, 3, 2, 2, 2, 4, 4, 6, 6, # 0x00
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6, # 0x10
        6, 6, 2, 8, 3, 3, 5, 5, 4, 2, 2, 2, 4, 4, 6, 6, # 0x20
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6, # 0x30
        6, 6, 2, 8, 3, 3, 5, 5, 3, 2, 2, 2, 3, 4, 6, 6, # 0x40
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6, # 0x50
        6, 6, 2, 8, 3, 3, 5, 5, 4, 2, 2, 2, 5, 4, 6, 6, # 0x60
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6, # 0x70
        2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4, # 0x80
        2, 6, 2, 6, 4, 4, 4, 4, 2, 5, 2, 2, 4, 5, 4, 4, # 0x90
        2, 6, 2, 6, 3, 3, 3, 3, 2, 2, 2, 2, 4, 4, 4, 4, # 0xA0
        2, 5, 2, 5, 4, 4, 4, 4, 2, 4, 2, 4, 4, 4, 4, 4, # 0xB0
        2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6, # 0xC0
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6, # 0xD0
        2, 6, 2, 8, 3, 3, 5, 5, 2, 2, 2, 2, 4, 4, 6, 6, # 0xE0
        2, 5, 2, 8, 4, 4, 6, 6, 2, 4, 2, 7, 4, 4, 7, 6  # 0xF0
    ]

    DECODE_TABLE = {
        # --- ADC ---
        0x61: {'instruction': 'ADC indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x65: {'instruction': 'ADC zp', 'code': 'val = cpu.read_byte({op0})\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x69: {'instruction': 'ADC imm', 'code': 'val = {op0}\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0x6D: {'instruction': 'ADC abs', 'code': 'val = cpu.read_byte({target})\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x71: {'instruction': 'ADC indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x75: {'instruction': 'ADC zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0x79: {'instruction': 'ADC absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x7D: {'instruction': 'ADC absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    lo_b = (cpu.A_reg & 0x0F) + (operand & 0x0F) + int(cpu.C_flag)\n    if lo_b > 0x09:\n        lo_b += 0x06\n    hi_b = (cpu.A_reg >> 4) + (operand >> 4) + (lo_b >> 4)\n    res = (lo_b & 0x0F) | (hi_b << 4)\n    cpu.Z_flag = ((cpu.A_reg + operand + int(cpu.C_flag)) & 0xFF) == 0\n    cpu.N_flag = bool(hi_b & 0x08)\n    cpu.V_flag = bool((~(cpu.A_reg ^ operand) & (cpu.A_reg ^ (res & 0xFF))) & 0x80)\n    cpu.C_flag = hi_b > 0x09\n    if cpu.C_flag:\n        res += 0x60\n    cpu.A_reg = res & 0xFF\nelse:\n    res = cpu.A_reg + operand + int(cpu.C_flag)\n    cpu.C_flag = res > 0xFF\n    cpu.V_flag = bool(~(cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- AND ---
        0x21: {'instruction': 'AND indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x25: {'instruction': 'AND zp', 'code': 'val = cpu.read_byte({op0})\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x29: {'instruction': 'AND imm', 'code': 'val = {op0}\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0x2D: {'instruction': 'AND abs', 'code': 'val = cpu.read_byte({target})\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x31: {'instruction': 'AND indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x35: {'instruction': 'AND zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0x39: {'instruction': 'AND absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x3D: {'instruction': 'AND absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.A_reg &= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- ASL ---
        0x06: {'instruction': 'ASL zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x80)\nval = (val << 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x0A: {'instruction': 'ASL A', 'code': 'val = cpu.A_reg\ncpu.C_flag = bool(val & 0x80)\nval = (val << 1) & 0xFF\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},
        0x0E: {'instruction': 'ASL abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x80)\nval = (val << 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0x16: {'instruction': 'ASL zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x80)\nval = (val << 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x1E: {'instruction': 'ASL absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x80)\nval = (val << 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- BCC ---
        0x90: {'instruction': 'BCC', 'code': 'if not cpu.C_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BCS ---
        0xB0: {'instruction': 'BCS', 'code': 'if cpu.C_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BEQ ---
        0xF0: {'instruction': 'BEQ', 'code': 'if cpu.Z_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BIT ---
        0x24: {'instruction': 'BIT zp', 'code': 'val = cpu.read_byte({op0})\ncpu.Z_flag = ((cpu.A_reg & val) == 0)\ncpu.V_flag = bool(val & 0x40)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x2C: {'instruction': 'BIT abs', 'code': 'val = cpu.read_byte({target})\ncpu.Z_flag = ((cpu.A_reg & val) == 0)\ncpu.V_flag = bool(val & 0x40)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- BMI ---
        0x30: {'instruction': 'BMI', 'code': 'if cpu.N_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BNE ---
        0xD0: {'instruction': 'BNE', 'code': 'if not cpu.Z_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BPL ---
        0x10: {'instruction': 'BPL', 'code': 'if not cpu.N_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BRK ---
        0x00: {'instruction': 'BRK', 'code': 'ret = {pc} + 2\ncpu.push_stack((ret >> 8) & 0xFF)\ncpu.push_stack(ret & 0xFF)\ncpu.push_stack(cpu.get_status(True))\ncpu.I_flag = True\ncpu.PC = cpu.read_word(0xFFFE)', 'cycles': 7, 'length': 1, 'JUMP': True},

        # --- BVC ---
        0x50: {'instruction': 'BVC', 'code': 'if not cpu.V_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- BVS ---
        0x70: {'instruction': 'BVS', 'code': 'if cpu.V_flag:\n    cpu.PC = {taken}\n    cpu.cycles += {extra}\nelse:\n    cpu.PC = {next}', 'cycles': 2, 'length': 2, 'JUMP': True},

        # --- CLC ---
        0x18: {'instruction': 'CLC', 'code': 'cpu.C_flag = False', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- CLD ---
        0xD8: {'instruction': 'CLD', 'code': 'cpu.D_flag = False', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- CLI ---
        0x58: {'instruction': 'CLI', 'code': 'cpu.I_flag = False', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- CLV ---
        0xB8: {'instruction': 'CLV', 'code': 'cpu.V_flag = False', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- CMP ---
        0xC1: {'instruction': 'CMP indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0xC5: {'instruction': 'CMP zp', 'code': 'val = cpu.read_byte({op0})\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xC9: {'instruction': 'CMP imm', 'code': 'val = {op0}\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xCD: {'instruction': 'CMP abs', 'code': 'val = cpu.read_byte({target})\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xD1: {'instruction': 'CMP indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0xD5: {'instruction': 'CMP zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0xD9: {'instruction': 'CMP absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xDD: {'instruction': 'CMP absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.C_flag = cpu.A_reg >= val\ncpu.Z_flag = (cpu.A_reg == val)\ncpu.N_flag = bool((cpu.A_reg - val) & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- CPX ---
        0xE0: {'instruction': 'CPX imm', 'code': 'val = {op0}\ncpu.C_flag = cpu.X_reg >= val\ncpu.Z_flag = (cpu.X_reg == val)\ncpu.N_flag = bool((cpu.X_reg - val) & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xE4: {'instruction': 'CPX zp', 'code': 'val = cpu.read_byte({op0})\ncpu.C_flag = cpu.X_reg >= val\ncpu.Z_flag = (cpu.X_reg == val)\ncpu.N_flag = bool((cpu.X_reg - val) & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xEC: {'instruction': 'CPX abs', 'code': 'val = cpu.read_byte({target})\ncpu.C_flag = cpu.X_reg >= val\ncpu.Z_flag = (cpu.X_reg == val)\ncpu.N_flag = bool((cpu.X_reg - val) & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- CPY ---
        0xC0: {'instruction': 'CPY imm', 'code': 'val = {op0}\ncpu.C_flag = cpu.Y_reg >= val\ncpu.Z_flag = (cpu.Y_reg == val)\ncpu.N_flag = bool((cpu.Y_reg - val) & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xC4: {'instruction': 'CPY zp', 'code': 'val = cpu.read_byte({op0})\ncpu.C_flag = cpu.Y_reg >= val\ncpu.Z_flag = (cpu.Y_reg == val)\ncpu.N_flag = bool((cpu.Y_reg - val) & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xCC: {'instruction': 'CPY abs', 'code': 'val = cpu.read_byte({target})\ncpu.C_flag = cpu.Y_reg >= val\ncpu.Z_flag = (cpu.Y_reg == val)\ncpu.N_flag = bool((cpu.Y_reg - val) & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- DEC ---
        0xC6: {'instruction': 'DEC zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\nval = (val - 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0xCE: {'instruction': 'DEC abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\nval = (val - 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0xD6: {'instruction': 'DEC zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\nval = (val - 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0xDE: {'instruction': 'DEC absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\nval = (val - 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- DEX ---
        0xCA: {'instruction': 'DEX imp', 'code': 'cpu.X_reg = (cpu.X_reg - 1) & 0xFF\ncpu.Z_flag = (cpu.X_reg == 0)\ncpu.N_flag = bool(cpu.X_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- DEY ---
        0x88: {'instruction': 'DEY imp', 'code': 'cpu.Y_reg = (cpu.Y_reg - 1) & 0xFF\ncpu.Z_flag = (cpu.Y_reg == 0)\ncpu.N_flag = bool(cpu.Y_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- EOR ---
        0x41: {'instruction': 'EOR indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x45: {'instruction': 'EOR zp', 'code': 'val = cpu.read_byte({op0})\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x49: {'instruction': 'EOR imm', 'code': 'val = {op0}\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0x4D: {'instruction': 'EOR abs', 'code': 'val = cpu.read_byte({target})\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x51: {'instruction': 'EOR indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x55: {'instruction': 'EOR zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0x59: {'instruction': 'EOR absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x5D: {'instruction': 'EOR absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.A_reg ^= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- INC ---
        0xE6: {'instruction': 'INC zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\nval = (val + 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0xEE: {'instruction': 'INC abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\nval = (val + 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0xF6: {'instruction': 'INC zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\nval = (val + 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0xFE: {'instruction': 'INC absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\nval = (val + 1) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- INX ---
        0xE8: {'instruction': 'INX imp', 'code': 'cpu.X_reg = (cpu.X_reg + 1) & 0xFF\ncpu.Z_flag = (cpu.X_reg == 0)\ncpu.N_flag = bool(cpu.X_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- INY ---
        0xC8: {'instruction': 'INY imp', 'code': 'cpu.Y_reg = (cpu.Y_reg + 1) & 0xFF\ncpu.Z_flag = (cpu.Y_reg == 0)\ncpu.N_flag = bool(cpu.Y_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- JMP ---
        0x4C: {'instruction': 'JMP abs', 'code': 'cpu.PC = {target}', 'cycles': 3, 'length': 3, 'JUMP': True},
        0x6C: {'instruction': 'JMP ind', 'code': 'lo = cpu.read_byte({target})\nhi = cpu.read_byte(({target} & 0xFF00) | (({target} + 1) & 0xFF))\ncpu.PC = (hi << 8) | lo', 'cycles': 5, 'length': 3, 'JUMP': True},

        # --- JSR ---
        0x20: {'instruction': 'JSR', 'code': 'ret = ({next} - 1) & 0xFFFF\ncpu.push_stack((ret >> 8) & 0xFF)\ncpu.push_stack(ret & 0xFF)\ncpu.PC = {target}', 'cycles': 6, 'length': 3, 'JUMP': True},

        # --- LDA ---
        0xA1: {'instruction': 'LDA indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0xA5: {'instruction': 'LDA zp', 'code': 'val = cpu.read_byte({op0})\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xA9: {'instruction': 'LDA imm', 'code': 'val = {op0}\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xAD: {'instruction': 'LDA abs', 'code': 'val = cpu.read_byte({target})\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xB1: {'instruction': 'LDA indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0xB5: {'instruction': 'LDA zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0xB9: {'instruction': 'LDA absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xBD: {'instruction': 'LDA absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- LDX ---
        0xA2: {'instruction': 'LDX imm', 'code': 'val = {op0}\ncpu.X_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xA6: {'instruction': 'LDX zp', 'code': 'val = cpu.read_byte({op0})\ncpu.X_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xAE: {'instruction': 'LDX abs', 'code': 'val = cpu.read_byte({target})\ncpu.X_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xB6: {'instruction': 'LDX zpy', 'code': 'val = cpu.read_byte(({op0} + cpu.Y_reg) & 0xFF)\ncpu.X_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0xBE: {'instruction': 'LDX absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.X_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- LDY ---
        0xA0: {'instruction': 'LDY imm', 'code': 'val = {op0}\ncpu.Y_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xA4: {'instruction': 'LDY zp', 'code': 'val = cpu.read_byte({op0})\ncpu.Y_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xAC: {'instruction': 'LDY abs', 'code': 'val = cpu.read_byte({target})\ncpu.Y_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xB4: {'instruction': 'LDY zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.Y_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0xBC: {'instruction': 'LDY absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.Y_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- LSR ---
        0x46: {'instruction': 'LSR zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x01)\nval = val >> 1\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x4A: {'instruction': 'LSR A', 'code': 'val = cpu.A_reg\ncpu.C_flag = bool(val & 0x01)\nval = val >> 1\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},
        0x4E: {'instruction': 'LSR abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x01)\nval = val >> 1\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0x56: {'instruction': 'LSR zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x01)\nval = val >> 1\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x5E: {'instruction': 'LSR absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\ncpu.C_flag = bool(val & 0x01)\nval = val >> 1\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- NOP ---
        0xEA: {'instruction': 'NOP', 'code': '', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- ORA ---
        0x01: {'instruction': 'ORA indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x05: {'instruction': 'ORA zp', 'code': 'val = cpu.read_byte({op0})\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x09: {'instruction': 'ORA imm', 'code': 'val = {op0}\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0x0D: {'instruction': 'ORA abs', 'code': 'val = cpu.read_byte({target})\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x11: {'instruction': 'ORA indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x15: {'instruction': 'ORA zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0x19: {'instruction': 'ORA absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x1D: {'instruction': 'ORA absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\ncpu.A_reg |= val\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- PHA ---
        0x48: {'instruction': 'PHA', 'code': 'cpu.push_stack(cpu.A_reg)', 'cycles': 3, 'length': 1, 'JUMP': False},

        # --- PHP ---
        0x08: {'instruction': 'PHP', 'code': 'cpu.push_stack(cpu.get_status(True))', 'cycles': 3, 'length': 1, 'JUMP': False},

        # --- PLA ---
        0x68: {'instruction': 'PLA', 'code': 'cpu.A_reg = cpu.pop_stack()\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 1, 'JUMP': False},

        # --- PLP ---
        0x28: {'instruction': 'PLP', 'code': 'st = cpu.pop_stack()\ncpu.N_flag = bool(st & 0x80)\ncpu.V_flag = bool(st & 0x40)\ncpu.D_flag = bool(st & 0x08)\ncpu.I_flag = bool(st & 0x04)\ncpu.Z_flag = bool(st & 0x02)\ncpu.C_flag = bool(st & 0x01)', 'cycles': 4, 'length': 1, 'JUMP': False},

        # --- ROL ---
        0x26: {'instruction': 'ROL zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x80)\nval = ((val << 1) | carry) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x2A: {'instruction': 'ROL A', 'code': 'val = cpu.A_reg\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x80)\nval = ((val << 1) | carry) & 0xFF\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},
        0x2E: {'instruction': 'ROL abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x80)\nval = ((val << 1) | carry) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0x36: {'instruction': 'ROL zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x80)\nval = ((val << 1) | carry) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x3E: {'instruction': 'ROL absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x80)\nval = ((val << 1) | carry) & 0xFF\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- ROR ---
        0x66: {'instruction': 'ROR zp', 'code': 'addr = {op0}\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x01)\nval = (val >> 1) | (carry << 7)\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0x6A: {'instruction': 'ROR A', 'code': 'val = cpu.A_reg\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x01)\nval = (val >> 1) | (carry << 7)\ncpu.A_reg = val\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},
        0x6E: {'instruction': 'ROR abs', 'code': 'addr = {target}\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x01)\nval = (val >> 1) | (carry << 7)\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 3, 'JUMP': False},
        0x76: {'instruction': 'ROR zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x01)\nval = (val >> 1) | (carry << 7)\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x7E: {'instruction': 'ROR absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\nval = cpu.read_byte(addr)\ncarry = int(cpu.C_flag)\ncpu.C_flag = bool(val & 0x01)\nval = (val >> 1) | (carry << 7)\ncpu.write_byte(addr, val)\ncpu.Z_flag = (val == 0)\ncpu.N_flag = bool(val & 0x80)', 'cycles': 7, 'length': 3, 'JUMP': False},

        # --- RTI ---
        0x40: {'instruction': 'RTI', 'code': 'st = cpu.pop_stack()\ncpu.N_flag = bool(st & 0x80)\ncpu.V_flag = bool(st & 0x40)\ncpu.D_flag = bool(st & 0x08)\ncpu.I_flag = bool(st & 0x04)\ncpu.Z_flag = bool(st & 0x02)\ncpu.C_flag = bool(st & 0x01)\nlo = cpu.pop_stack()\nhi = cpu.pop_stack()\ncpu.PC = (hi << 8) | lo', 'cycles': 6, 'length': 1, 'JUMP': True},

        # --- RTS ---
        0x60: {'instruction': 'RTS', 'code': 'lo = cpu.pop_stack()\nhi = cpu.pop_stack()\ncpu.PC = (((hi << 8) | lo) + 1) & 0xFFFF', 'cycles': 6, 'length': 1, 'JUMP': True},

        # --- SBC ---
        0xE1: {'instruction': 'SBC indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\nval = cpu.read_byte((hi << 8) | lo)\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0xE5: {'instruction': 'SBC zp', 'code': 'val = cpu.read_byte({op0})\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0xE9: {'instruction': 'SBC imm', 'code': 'val = {op0}\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 2, 'JUMP': False},
        0xED: {'instruction': 'SBC abs', 'code': 'val = cpu.read_byte({target})\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xF1: {'instruction': 'SBC indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\nval = cpu.read_byte((((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 5, 'length': 2, 'JUMP': False},
        0xF5: {'instruction': 'SBC zpx', 'code': 'val = cpu.read_byte(({op0} + cpu.X_reg) & 0xFF)\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0xF9: {'instruction': 'SBC absy', 'code': 'val = cpu.read_byte(({target} + cpu.Y_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0xFD: {'instruction': 'SBC absx', 'code': 'val = cpu.read_byte(({target} + cpu.X_reg) & 0xFFFF)\noperand = val\nif cpu.D_flag:\n    val_a = cpu.A_reg\n    b = operand\n    res = val_a - b - (1 - int(cpu.C_flag))\n    lo_b = (val_a & 0x0F) - (b & 0x0F) - (1 - int(cpu.C_flag))\n    if lo_b < 0:\n        lo_b -= 6\n    hi_b = (val_a >> 4) - (b >> 4) - (1 if lo_b < 0 else 0)\n    if hi_b < 0:\n        hi_b -= 6\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((val_a ^ b) & (val_a ^ res) & 0x80)\n    res_bcd = ((hi_b << 4) | (lo_b & 0x0F)) & 0xFF\n    cpu.Z_flag = (res & 0xFF) == 0\n    cpu.N_flag = bool(res & 0x80)\n    cpu.A_reg = res_bcd\nelse:\n    res = cpu.A_reg - operand - (1 - int(cpu.C_flag))\n    cpu.C_flag = res >= 0\n    cpu.V_flag = bool((cpu.A_reg ^ operand) & (cpu.A_reg ^ res) & 0x80)\n    cpu.A_reg = res & 0xFF\n    cpu.Z_flag = (cpu.A_reg == 0)\n    cpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 4, 'length': 3, 'JUMP': False},

        # --- SEC ---
        0x38: {'instruction': 'SEC', 'code': 'cpu.C_flag = True', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- SED ---
        0xF8: {'instruction': 'SED', 'code': 'cpu.D_flag = True', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- SEI ---
        0x78: {'instruction': 'SEI', 'code': 'cpu.I_flag = True', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- STA ---
        0x81: {'instruction': 'STA indx', 'code': 'ptr = ({op0} + cpu.X_reg) & 0xFF\nlo = cpu.read_byte(ptr)\nhi = cpu.read_byte((ptr + 1) & 0xFF)\naddr = (hi << 8) | lo\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x85: {'instruction': 'STA zp', 'code': 'addr = {op0}\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x8D: {'instruction': 'STA abs', 'code': 'addr = {target}\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x91: {'instruction': 'STA indy', 'code': 'lo = cpu.read_byte({op0})\nhi = cpu.read_byte(({op0} + 1) & 0xFF)\naddr = (((hi << 8) | lo) + cpu.Y_reg) & 0xFFFF\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 6, 'length': 2, 'JUMP': False},
        0x95: {'instruction': 'STA zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 4, 'length': 2, 'JUMP': False},
        0x99: {'instruction': 'STA absy', 'code': 'addr = ({target} + cpu.Y_reg) & 0xFFFF\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 5, 'length': 3, 'JUMP': False},
        0x9D: {'instruction': 'STA absx', 'code': 'addr = ({target} + cpu.X_reg) & 0xFFFF\ncpu.write_byte(addr, cpu.A_reg)', 'cycles': 5, 'length': 3, 'JUMP': False},

        # --- STX ---
        0x86: {'instruction': 'STX zp', 'code': 'addr = {op0}\ncpu.write_byte(addr, cpu.X_reg)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x8E: {'instruction': 'STX abs', 'code': 'addr = {target}\ncpu.write_byte(addr, cpu.X_reg)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x96: {'instruction': 'STX zpy', 'code': 'addr = ({op0} + cpu.Y_reg) & 0xFF\ncpu.write_byte(addr, cpu.X_reg)', 'cycles': 4, 'length': 2, 'JUMP': False},

        # --- STY ---
        0x84: {'instruction': 'STY zp', 'code': 'addr = {op0}\ncpu.write_byte(addr, cpu.Y_reg)', 'cycles': 3, 'length': 2, 'JUMP': False},
        0x8C: {'instruction': 'STY abs', 'code': 'addr = {target}\ncpu.write_byte(addr, cpu.Y_reg)', 'cycles': 4, 'length': 3, 'JUMP': False},
        0x94: {'instruction': 'STY zpx', 'code': 'addr = ({op0} + cpu.X_reg) & 0xFF\ncpu.write_byte(addr, cpu.Y_reg)', 'cycles': 4, 'length': 2, 'JUMP': False},

        # --- TAX ---
        0xAA: {'instruction': 'TAX', 'code': 'cpu.X_reg = cpu.A_reg\ncpu.Z_flag = (cpu.X_reg == 0)\ncpu.N_flag = bool(cpu.X_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- TAY ---
        0xA8: {'instruction': 'TAY', 'code': 'cpu.Y_reg = cpu.A_reg\ncpu.Z_flag = (cpu.Y_reg == 0)\ncpu.N_flag = bool(cpu.Y_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- TSX ---
        0xBA: {'instruction': 'TSX', 'code': 'cpu.X_reg = cpu.stack_pointer\ncpu.Z_flag = (cpu.X_reg == 0)\ncpu.N_flag = bool(cpu.X_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- TXA ---
        0x8A: {'instruction': 'TXA', 'code': 'cpu.A_reg = cpu.X_reg\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- TXS ---
        0x9A: {'instruction': 'TXS', 'code': 'cpu.stack_pointer = cpu.X_reg', 'cycles': 2, 'length': 1, 'JUMP': False},

        # --- TYA ---
        0x98: {'instruction': 'TYA', 'code': 'cpu.A_reg = cpu.Y_reg\ncpu.Z_flag = (cpu.A_reg == 0)\ncpu.N_flag = bool(cpu.A_reg & 0x80)', 'cycles': 2, 'length': 1, 'JUMP': False},

    }