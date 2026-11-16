import os
import sys
import time
from Interpreter import Interpreter

script_dir = os.path.dirname(os.path.abspath(__file__))
bin_file_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(script_dir, "6502_functional_test.bin")

if not os.path.exists(bin_file_path):
    print(f"[ERROR] ROM file not found: {bin_file_path}")
    sys.exit(1)

interpreter = Interpreter()
print(f"Loading binary test suite from: {bin_file_path}")

with open(bin_file_path, "rb") as f:
    rom_data = f.read()

# Load straight into RAM (the .bin is a full 64K image starting at 0x0000)
n = min(len(rom_data), 65536)
interpreter.emu.ram[0:n] = list(rom_data[:n])

interpreter.emu.pc = 0x0400  # Klaus test start vector
SUCCESS_PC = 0x3469

print("Starting 6502 functional test execution on classic interpreter...")

steps = 0
total_cycles = 0
start = time.perf_counter()
same_pc_counter = 0

try:
    while True:
        pc = interpreter.emu.pc
        cycles = interpreter.step()
        steps += 1
        total_cycles += cycles

        # Klaus traps (success and failure) are a jump/branch to self
        if interpreter.emu.pc == pc:
            same_pc_counter += 1
            if same_pc_counter > 3:
                break
        else:
            same_pc_counter = 0

        if steps % 5_000_000 == 0:
            print(f"[PROGRESS] {steps:,} instructions... PC: 0x{interpreter.emu.pc:04X}")

except Exception as e:
    print(f"\nTest crashed with error: {e!r}")
    import traceback
    traceback.print_exc()

elapsed = time.perf_counter() - start
print("\n--- Benchmark Completed ---")
print(f"Total Instructions: {steps:,}")
print(f"Total Cycles: {total_cycles:,}")
print(f"Time Taken: {elapsed:.2f} seconds")
if elapsed > 0:
    print(f"Throughput: {steps / elapsed:,.0f} Instructions/sec")
    print(f"CPU Frequency: {total_cycles / elapsed / 1_000_000:.2f} MHz (simulated)")
print(f"Final PC: {hex(interpreter.emu.pc)}")

if interpreter.emu.pc == SUCCESS_PC:
    print("\nSUCCESS: All functional tests passed with cycle tracking! 🎉")
else:
    print(f"\nTrapped at PC: {hex(interpreter.emu.pc)}  (failure - look it up in 6502_functional_test.lst)")
    print(f"Last opcode: 0x{interpreter.last_opcode:02X} at 0x{interpreter.last_pc:04X}")