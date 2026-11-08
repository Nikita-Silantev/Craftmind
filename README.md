# Craftmind

Craftmind is an early prototype for playing Minecraft Java Edition with a local AI agent. The target is a named, Steve-skinned companion that follows chat commands in a single-player world.

Development currently happens on `dev`. The bot spawns through Fabric Carpet and reads compact observations before each action: position, health, food, inventory, targeted block, and nearby objects. The agent can take several steps per task. Block placement, crafting, and pathfinding are not implemented yet, so it must explain why it cannot build a house. See [plan.md](plan.md) for the roadmap.

## Build the prototype mod

Requires JDK 21 or newer. From `mod/`, run `./gradlew build` on macOS/Linux or `gradlew.bat build` on Windows. The mod targets Minecraft 1.21.11 with Fabric API and Carpet 1.4.194. The JAR is produced in `mod/build/libs/`.

## Status

See [the Windows first-test guide](docs/first-test.md). In-game behavior has not yet been manually verified; the mod builds and Python tests pass.
