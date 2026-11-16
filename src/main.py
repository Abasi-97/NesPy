import time

import pygame

from cpu import CPU
from bus import Bus
from ppu import PPU
from loader import ROM
from mapper import Mapper0, Mapper1
from JIT import AsyncSuperblockJIT

KEYMAP = [
    (pygame.K_x, 0x01),        # A
    (pygame.K_z, 0x02),        # B
    (pygame.K_RSHIFT, 0x04),   # Select
    (pygame.K_RETURN, 0x08),   # Start
    (pygame.K_UP, 0x10),
    (pygame.K_DOWN, 0x20),
    (pygame.K_LEFT, 0x40),
    (pygame.K_RIGHT, 0x80),
]

SCALE = 3
UNCAPPED = False          # True = ignore the 60fps limiter (benchmarking)
MAX_FRAMESKIP = 3         # when the emulator falls behind, skip drawing up to this many frames in a row
ROM_PATH = r"C:\Users\HP\Desktop\NesPY\src\Final Fantasy (USA).nes"
FRAME_TIME = 1.0 / 60.0988
TICK_EVERY = 32           # CPU cycles between PPU updates (smaller = better vblank / sprite-0 timing)

MAPPER_CLASSES = {0: Mapper0, 1: Mapper1}


def nmi(cpu):
    ram = cpu.ram
    sp = cpu.stack_pointer
    pc = cpu.PC
    ram[0x100 + sp] = pc >> 8
    ram[0x100 + ((sp - 1) & 0xFF)] = pc & 0xFF
    ram[0x100 + ((sp - 2) & 0xFF)] = cpu.get_status(False)
    cpu.stack_pointer = (sp - 3) & 0xFF
    cpu.I_flag = True
    cpu.PC = cpu.read_word(0xFFFA)
    cpu.cycles += 7


def run(cpu, jit, bus, screen, frame_surf, fb):
    get_block = jit.get_superblock
    ppu = bus.ppu
    size = (256 * SCALE, 240 * SCALE)

    frames_since = 0
    drawn_since = 0
    skipped = 0
    t_ppu = 0.0
    nmis = 0

    last_tick = cpu.cycles
    last_frame = ppu.frame_counter
    t_last = next_t = time.perf_counter()

    while cpu.running:
        pc = cpu.PC
        block = jit.cache.get(pc)      # the dict is swapped on PRG bank changes
        if block is None:
            block = get_block(pc)
            if block is None:
                cpu.running = False
                break
        block(cpu)

        elapsed = cpu.cycles - last_tick
        if elapsed < TICK_EVERY:
            continue
        last_tick = cpu.cycles

        pc_before_nmi = cpu.PC          # where the game really is (PC after an NMI is always the NMI vector)
        if ppu.tick(elapsed):
            nmi(cpu)
            nmis += 1

        new_frames = ppu.frame_counter - last_frame
        if new_frames <= 0:
            continue
        last_frame = ppu.frame_counter
        frames_since += new_frames

        # window events + controller
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                cpu.running = False
        if not cpu.running:
            break

        keys = pygame.key.get_pressed()
        state = 0
        for key, bit in KEYMAP:
            if keys[key]:
                state |= bit
        bus.controller.buttons = state

        # pacing: draw if we are on schedule, skip drawing if we have fallen behind
        now = time.perf_counter()
        next_t += FRAME_TIME * new_frames
        behind = now - next_t
        if behind > 0.25:               # far behind (window drag, load hitch): resync
            next_t = now
            behind = 0.0

        if behind > FRAME_TIME and skipped < MAX_FRAMESKIP and not UNCAPPED:
            skipped += 1                # skip the expensive render, keep emulating
        else:
            skipped = 0
            t0 = time.perf_counter()
            ppu.render_frame(fb)
            t_ppu += time.perf_counter() - t0
            screen.blit(pygame.transform.scale(frame_surf, size), (0, 0))
            pygame.display.flip()
            drawn_since += 1

            if not UNCAPPED:
                rem = next_t - time.perf_counter()
                if rem > 0.002:
                    time.sleep(rem - 0.002)
                while time.perf_counter() < next_t:
                    pass

        now = time.perf_counter()
        if now - t_last >= 1.0:
            dt = now - t_last
            j = bus.controller
            mp = bus.mapper
            mmc1 = f" | MMC1 ctl={mp.control:02X} prg={mp.prg_bank:X} chr={mp.chr_bank_0:02X}/{mp.chr_bank_1:02X}" \
                if hasattr(mp, 'control') else ""
            print(f"{frames_since / dt:5.1f} emu fps | {drawn_since / dt:5.1f} drawn | "
                  f"render {1000 * t_ppu / max(drawn_since, 1):4.1f} ms | "
                  f"PC=0x{pc_before_nmi:04X} | nmi/s {nmis / dt:3.0f} | "
                  f"joy strobes {j.writes} reads {j.reads} btn={j.buttons:02X} | "
                  f"PPUCTRL={ppu.registers[0]:02X} MASK={ppu.registers[1]:02X}{mmc1} | "
                  f"compiled {jit.compile_count}")
            j.writes = j.reads = 0
            nmis = 0
            frames_since = drawn_since = 0
            t_ppu = 0.0
            t_last = now

    jit.shutdown()


def main():
    rom = ROM(ROM_PATH)
    if rom.mapper_id not in MAPPER_CLASSES:
        raise SystemExit(f"Mapper {rom.mapper_id} is not supported (only {sorted(MAPPER_CLASSES)}).")

    cpu = CPU(None)                                 # type: ignore
    ppu = PPU(rom.chr_rom, rom.mirroring)
    bus = Bus(cpu, ppu)
    cpu.bus = bus
    bus.mapper = MAPPER_CLASSES[rom.mapper_id](rom.prg_rom, rom.chr_rom)
    jit = AsyncSuperblockJIT(cpu)
    cpu.PC = cpu.read_word(0xFFFC)                  # reset vector

    pygame.init()
    screen = pygame.display.set_mode((256 * SCALE, 240 * SCALE))
    pygame.display.set_caption("NesPY")

    # The surface shares memory with fb, so updating fb in place updates the surface (no per-frame copy).
    fb = bytearray(256 * 240 * 3)
    frame_surf = pygame.image.frombuffer(fb, (256, 240), "RGB")

    try:
        run(cpu, jit, bus, screen, frame_surf, fb)
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()