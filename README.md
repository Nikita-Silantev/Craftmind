# Craftmind

Craftmind is an early prototype for playing Minecraft Java Edition with a local AI agent. The target is a named, Steve-skinned companion that follows chat commands in a single-player world.

Development currently happens on `dev`. The first milestone is a Fabric mod with `/craftmind status`; it does not spawn or control an agent yet. See [plan.md](plan.md) for the roadmap.

## Build the prototype mod

Requires JDK 21 or newer. From `mod/`, run `./gradlew build` on macOS/Linux or `gradlew.bat build` on Windows. The mod targets Minecraft 1.21.11 with Fabric API. The JAR is produced in `mod/build/libs/`.

## Status

The chat command `/craftmind status` confirms that the mod loaded. This is a development check, not a playable AI release.
