# Craftmind

Craftmind is an early prototype for playing Minecraft Java Edition with a local AI agent. The target is a named, Steve-skinned companion that follows chat commands in a single-player world.

Development currently happens on `dev`. The bot spawns through Fabric Carpet and reads observations before each action: position, health, food, the block under its feet, the targeted block, inventory, and nearby objects. The Python agent turns these observations into a concise description for the local model. It can execute a task in phases and replan after observing the result. Movement can target a measured distance. The bot can place a selected block in front of it or against a targeted face; in creative mode it can take registered block items into its hotbar. Crafting and pathfinding are not implemented yet. See [plan.md](plan.md) for the roadmap.

## Build the prototype mod

Requires JDK 21 or newer. From `mod/`, run `./gradlew build` on macOS/Linux or `gradlew.bat build` on Windows. The mod targets Minecraft 1.21.11 with Fabric API and Carpet 1.4.194. The JAR is produced in `mod/build/libs/`.

## Status

See [the Windows first-test guide](docs/first-test.md). Bot spawning and the phased planner have been tested manually. Measured movement, block selection, and placement still need an in-game test.
