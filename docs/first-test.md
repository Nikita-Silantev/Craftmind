# First test on Windows

This is a prototype. Use a separate superflat creative world for the next test. The bot can move a measured number of blocks, turn, sprint, jump, mine, and place blocks. In creative mode it can acquire registered block items into its hotbar; in survival it uses blocks already in its inventory. Crafting and pathfinding are not implemented yet.

## Install

1. Install Minecraft Java Edition **1.21.11** with [Fabric Loader](https://fabricmc.net/use/installer/).
2. Put [Fabric API for 1.21.11](https://modrinth.com/mod/fabric-api/versions?g=1.21.11), [Carpet 1.4.194](https://modrinth.com/mod/carpet/version/1.4.194), and `craftmind-mod-0.1.0.jar` in `mods`. Build the latter from `mod/` with `gradlew.bat build`; find it under `mod/build/libs/`.
3. Install Python 3.11 or newer. Open a terminal in `agent` and run `py -m craftmind.cli --mode scripted`.

## In-game check

1. Open a single-player world. `/craftmind status` should show `127.0.0.1:8765`.
2. Run `/craftmind spawn`. A player named `Craftmind` should appear.
3. Send `@Craftmind forward` in normal chat. The bot should move for about two seconds, stop, and reply in chat.
4. Try `@Craftmind jump`, `@Craftmind stop`, and `@Craftmind state`.

After updating `dev`, rebuild the JAR, replace the old file in `mods`, and restart Minecraft. Stop the old Python agent with Ctrl+C, then start the new one. Use `/craftmind spawn` to create the bot; `@Craftmind spawn` is a chat task, not the spawn command. In Ollama mode the model plans in short phases, with a default task limit of 128 actions (`--max-steps` changes it). The agent keeps only the last 16 actions in model context and stops repeated left-right turning before the limit. `@Craftmind stop` cancels the current task, including long movement.

If the bot does not spawn, check that Carpet and its `/player` command are available. If the agent reports a connection error, keep the world open and check `/craftmind status`. The error screenshot, `logs/latest.log`, and agent output will help debug the first run.

## Local model check

Install [Ollama](https://ollama.com/download/windows), then run `ollama pull qwen3:14b`. Stop the scripted agent and run `py -3.14 -m craftmind.cli --mode ollama --model qwen3:14b` from `agent`. In a superflat creative world, try `@Craftmind walk 15 blocks forward`, then ask it to place `minecraft:oak_planks` in front, `minecraft:cobblestone` to the right, and planks to the left. The 15-block movement should be one plan step measured by position. Placement needs a free adjacent cell with support underneath. Afterward, test survival with the required blocks already in the bot's inventory. If it stops, capture its in-game reply and the Python log lines containing `Model plan` and `Step`.
