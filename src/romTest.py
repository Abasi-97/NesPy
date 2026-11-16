from AOT import AOT
from bus import Bus
from ppu import PPU
from mapper import Mapper0, Mapper1
from loader import ROM
import time


rom = ROM(r"C:\Users\HP\Desktop\chip8PY\core\src\Tetris (USA).nes")

if rom.mapper_id == 0:
    cart_mapper = Mapper0(rom.prg_rom, rom.chr_rom)
elif rom.mapper_id == 1:
    cart_mapper = Mapper1(rom.prg_rom, rom.chr_rom)
else:
    raise NotImplementedError(f"Mapper {rom.mapper_id} not supported yet!")

ppu = PPU(chr_rom=rom.chr_rom)
bus = Bus(cpu=None, ppu=ppu)
bus.mapper = cart_mapper

cpu = AOT(bus=bus)
bus.cpu = cpu

cpu.reset()
print("[System] Mapper 1 successfully linked to Bus and AOT CPU!")

# Let's override emulationLoop temporarily to watch the instructions stream by!
print("\n--- Starting Tetris Execution Trace ---")
step_count = 0
print("\n--- Running Tetris past VBlank Polling ---")
step_count = 0
# Run up to 5,000 steps to let CPU cycle count cross the ~26,000 cycle VBlank line
print("\n--- Running Tetris through Bank Configuration ---")
print("\n--- Running Tetris through Bank Configuration ---")
step_count = 0
print("\n--- Tracing instructions leading to 0xAA45 ---")
# Let's run until PC hits the subroutine call just before 0xAA4D

print("\n--- Disassembling subroutine at 0xE000 ---")
print("\n--- Disassembling subroutine at 0xE216 ---")
print("\n--- Forcing startup counter to bypass boot lock ---")
print("\n--- Forcing boot sync flags ---")
print("\n--- Running Tetris through VRAM Upload Phase ---")
print("\n--- Tracing stack and PC leading up to crash ---")
print("\n--- Running Tetris with pointer table seed ---")
print("\n--- Running Tetris with direct vector table seed ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with helper-routine hook ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with 0xAC00 vector pre-seed ---")
step_count = 0
bypassed_init = False

print("\n--- Tracing Stack and Return Addresses ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with stack alignment hook ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with RTS bypass hook ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris into main loop ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with Dispatcher Pointer Seed ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris into Extended Game Loop ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris through Full Mapper Config & Into Game Loop ---")
step_count = 0
bypassed_init = False

print("\n--- Tracing Dispatcher Pointer Extraction (0xAC86) ---")
step_count = 0
bypassed_init = False

print("\n--- Tracing Dispatcher Pointer Extraction (Always Active) ---")
step_count = 0
bypassed_init = False
log_count = 0

print("\n--- Running Tetris with Dynamic Pointer Guard ---")
step_count = 0
bypassed_init = False

print("\n--- Running Tetris with Extended Dynamic Guard (300k Steps) ---")
step_count = 0
bypassed_init = False
seen_banks = set()



print("\n--- Running Tetris at Full Speed (No Traces) ---")

# Let's run a clean 1,000,000 steps without any pythonic if-checks per tick
target_steps = 1000000
step_count = 0

start_time = time.perf_counter()

while cpu.running and step_count < target_steps:
    cpu.step()
    step_count += 1

end_time = time.perf_counter()
elapsed = end_time - start_time
ips = step_count / elapsed if elapsed > 0 else 0

print(f"Execution finished:")
print(f" - Total Steps: {step_count:,}")
print(f" - Time Elapsed: {elapsed:.3f} seconds")
print(f" - Speed: {ips:,.0f} Instructions Per Second (IPS)")
print(f" - Final PC: 0x{cpu.PC:04X}")