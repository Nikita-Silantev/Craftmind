# First test on Windows

This is a prototype. Test scripted mode first, then Ollama. Use a separate test world: the bot can only perform simple movements for now.

## Install

1. Install Minecraft Java Edition **1.21.11** with [Fabric Loader](https://fabricmc.net/use/installer/).
2. Put [Fabric API for 1.21.11](https://modrinth.com/mod/fabric-api/versions?g=1.21.11), [Carpet 1.4.194](https://modrinth.com/mod/carpet/version/1.4.194), and `craftmind-mod-0.1.0.jar` in `mods`. Build the latter from `mod/` with `gradlew.bat build`; find it under `mod/build/libs/`.
3. Install Python 3.11 or newer. Open a terminal in `agent` and run `py -m craftmind.cli --mode scripted`.

## In-game check

1. Open a single-player world. `/craftmind status` should show `127.0.0.1:8765`.
2. Run `/craftmind spawn`. A player named `Craftmind` should appear.
3. Send `@Craftmind forward` in normal chat. The bot should move for about two seconds, stop, and reply in chat.
4. Try `@Craftmind jump`, `@Craftmind stop`, and `@Craftmind state`.

If the bot does not spawn, check that Carpet and its `/player` command are available. If the agent reports a connection error, keep the world open and check `/craftmind status`. The error screenshot, `logs/latest.log`, and agent output will help debug the first run.

## Local model check

Install [Ollama](https://ollama.com/download/windows), then run `ollama pull qwen3:8b`. Stop the scripted agent and run `py -m craftmind.cli --mode ollama --model qwen3:8b` from `agent`. Repeat a simple chat command. The model currently selects one action from a short list. This checks model connectivity, not full autonomy.
