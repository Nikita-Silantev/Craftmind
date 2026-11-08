package dev.craftmind;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.mojang.brigadier.CommandDispatcher;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.message.v1.ServerMessageEvents;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.PermissionSet;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public final class CraftmindMod implements ModInitializer {
    public static final String MOD_ID = "craftmind";
    private static final String BOT_NAME = "Craftmind";
    private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
    private static final Gson GSON = new Gson();
    private final ConcurrentLinkedQueue<Map<String, String>> events = new ConcurrentLinkedQueue<>();
    private HttpServer bridge;
    private ExecutorService bridgeExecutor;
    private MinecraftServer server;
    private long ticks;
    private long stopAtTick;

    @Override
    public void onInitialize() {
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) -> registerCommands(dispatcher));
        ServerMessageEvents.CHAT_MESSAGE.register((message, sender, params) -> {
            String content = message.signedContent().trim();
            if (content.regionMatches(true, 0, "@" + BOT_NAME + " ", 0, BOT_NAME.length() + 2)) {
                String task = content.substring(BOT_NAME.length() + 2).trim();
                if (!task.isEmpty()) {
                    events.add(Map.of("player", sender.getGameProfile().name(), "task", task));
                    sender.sendSystemMessage(Component.literal("[Craftmind] Задача получена: " + task));
                }
            }
        });
        ServerTickEvents.END_SERVER_TICK.register(s -> {
            ticks++;
            if (stopAtTick > 0 && ticks >= stopAtTick) {
                stopAtTick = 0;
                ServerPlayer bot = s.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot != null) runCarpet(bot.createCommandSourceStack(), "player " + BOT_NAME + " stop");
            }
        });
        ServerLifecycleEvents.SERVER_STARTED.register(this::startBridge);
        ServerLifecycleEvents.SERVER_STOPPING.register(s -> stopBridge());
        LOGGER.info("Craftmind initialized");
    }

    private void registerCommands(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("craftmind")
            .then(Commands.literal("status").executes(ctx -> {
                boolean online = bridge != null;
                ctx.getSource().sendSuccess(() -> Component.literal("Craftmind bridge: " + (online ? "127.0.0.1:8765" : "stopped")), false);
                return 1;
            }))
            .then(Commands.literal("spawn").executes(ctx -> {
                if (ctx.getSource().getPlayer() == null) return 0;
                if (server.getPlayerList().getPlayerByName(BOT_NAME) != null) {
                    ctx.getSource().sendFailure(Component.literal("Craftmind already exists"));
                    return 0;
                }
                int result = runCarpet(ctx.getSource(), "player " + BOT_NAME + " spawn");
                if (result == 0) ctx.getSource().sendFailure(Component.literal("Install Fabric Carpet and enable /player"));
                return result;
            })));
    }

    private int runCarpet(CommandSourceStack source, String command) {
        if (server.getCommands().getDispatcher().getRoot().getChild("player") == null) return 0;
        server.getCommands().performPrefixedCommand(source.withPermission(PermissionSet.ALL_PERMISSIONS), command);
        return 1;
    }

    private void startBridge(MinecraftServer minecraftServer) {
        this.server = minecraftServer;
        try {
            bridge = HttpServer.create(new InetSocketAddress("127.0.0.1", 8765), 0);
            bridge.createContext("/events", this::handleEvents);
            bridge.createContext("/state", this::handleState);
            bridge.createContext("/action", this::handleAction);
            bridge.createContext("/reply", this::handleReply);
            bridgeExecutor = Executors.newCachedThreadPool();
            bridge.setExecutor(bridgeExecutor);
            bridge.start();
            LOGGER.info("Craftmind bridge listening on 127.0.0.1:8765");
        } catch (IOException e) {
            LOGGER.error("Cannot start Craftmind bridge", e);
        }
    }

    private void stopBridge() {
        if (bridge != null) bridge.stop(0);
        if (bridgeExecutor != null) bridgeExecutor.shutdownNow();
        bridge = null;
        bridgeExecutor = null;
        server = null;
        stopAtTick = 0;
        events.clear();
    }

    private void handleEvents(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("GET")) { respond(exchange, 405, Map.of("error", "GET required")); return; }
        List<Map<String, String>> batch = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            Map<String, String> event = events.poll();
            if (event == null) break;
            batch.add(event);
        }
        respond(exchange, 200, Map.of("events", batch));
    }

    private void handleState(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("GET")) { respond(exchange, 405, Map.of("error", "GET required")); return; }
        try {
            Map<String, Object> state = server.submit(() -> {
                ServerPlayer bot = server.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot == null) return Map.<String, Object>of("bot", "absent");
                return observe(bot);
            }).get(3, TimeUnit.SECONDS);
            respond(exchange, 200, state);
        } catch (Exception e) { respond(exchange, 500, Map.of("error", e.toString())); }
    }

    private Map<String, Object> observe(ServerPlayer bot) {
        Map<String, Object> state = new HashMap<>();
        state.put("bot", "present");
        state.put("x", Math.round(bot.getX() * 10) / 10.0);
        state.put("y", Math.round(bot.getY() * 10) / 10.0);
        state.put("z", Math.round(bot.getZ() * 10) / 10.0);
        state.put("facing_degrees", bot.getYRot());
        state.put("health", bot.getHealth());
        state.put("food", bot.getFoodData().getFoodLevel());
        state.put("sprinting", bot.isSprinting());
        state.put("game_mode", bot.gameMode.getGameModeForPlayer().getName());
        state.put("on_ground", bot.onGround());
        BlockPos below = BlockPos.containing(bot.getX(), bot.getY() - 0.1, bot.getZ());
        state.put("block_below", Map.of(
            "id", BuiltInRegistries.BLOCK.getKey(bot.level().getBlockState(below).getBlock()).toString(),
            "x", below.getX(), "y", below.getY(), "z", below.getZ()));
        if (bot.pick(6, 1.0f, false) instanceof BlockHitResult hit) {
            BlockPos target = hit.getBlockPos();
            state.put("target_block", Map.of(
                "id", BuiltInRegistries.BLOCK.getKey(bot.level().getBlockState(target).getBlock()).toString(),
                "x", target.getX(), "y", target.getY(), "z", target.getZ()));
        }

        Map<String, Integer> inventory = new HashMap<>();
        for (int slot = 0; slot < bot.getInventory().getContainerSize(); slot++) {
            ItemStack stack = bot.getInventory().getItem(slot);
            if (!stack.isEmpty()) {
                String id = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                inventory.merge(id, stack.getCount(), Integer::sum);
            }
        }
        state.put("inventory", inventory);

        BlockPos center = bot.blockPosition();
        Map<String, Integer> counts = new HashMap<>();
        Map<String, BlockPos> nearest = new HashMap<>();
        for (int dx = -5; dx <= 5; dx++) {
            for (int dy = -2; dy <= 3; dy++) {
                for (int dz = -5; dz <= 5; dz++) {
                    BlockPos pos = center.offset(dx, dy, dz);
                    var block = bot.level().getBlockState(pos);
                    if (block.isAir()) continue;
                    String id = BuiltInRegistries.BLOCK.getKey(block.getBlock()).toString();
                    counts.merge(id, 1, Integer::sum);
                    if (!nearest.containsKey(id) || pos.distSqr(center) < nearest.get(id).distSqr(center)) nearest.put(id, pos);
                }
            }
        }
        List<Map<String, Object>> blocks = new ArrayList<>();
        counts.keySet().stream().sorted(Comparator.comparingInt((String id) -> {
            if (id.contains("_log") || id.contains("_ore") || id.contains("chest") || id.contains("lava") || id.contains("water")) return 0;
            return 1;
        }).thenComparing(id -> id)).limit(24).forEach(id -> {
            BlockPos pos = nearest.get(id);
            blocks.add(Map.of("id", id, "count", counts.get(id), "x", pos.getX(), "y", pos.getY(), "z", pos.getZ()));
        });
        state.put("nearby_blocks", blocks);

        List<Map<String, Object>> entities = new ArrayList<>();
        for (Entity entity : bot.level().getEntities(bot, AABB.ofSize(bot.position(), 12, 8, 12), e -> true)) {
            if (entities.size() >= 12) break;
            entities.add(Map.of("id", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString(),
                "x", Math.round(entity.getX()), "y", Math.round(entity.getY()), "z", Math.round(entity.getZ())));
        }
        state.put("nearby_entities", entities);
        state.put("observation_note", "Nearby blocks/entities are a local sensor, including objects behind walls; not a camera image.");
        return state;
    }

    private void handleAction(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("POST")) { respond(exchange, 405, Map.of("error", "POST required")); return; }
        byte[] body = exchange.getRequestBody().readNBytes(4096);
        try {
            JsonObject input = GSON.fromJson(new String(body, StandardCharsets.UTF_8), JsonObject.class);
            String action = input.get("action").getAsString();
            int requestedMs = input.has("duration_ms") ? input.get("duration_ms").getAsInt() : 1200;
            int durationMs = Math.max(200, Math.min(3000, requestedMs));
            if (!List.of("forward", "back", "left", "right", "sprint_forward", "turn_left", "turn_right", "look_down", "look_up", "stop", "jump", "attack", "mine", "use").contains(action)) {
                respond(exchange, 400, Map.of("error", "unknown action")); return;
            }
            int result = server.submit(() -> {
                ServerPlayer bot = server.getPlayerList().getPlayerByName(BOT_NAME);
                if (bot == null) return 0;
                CommandSourceStack source = bot.createCommandSourceStack();
                String command = switch (action) {
                    case "jump" -> "jump once";
                    case "attack" -> "attack once";
                    case "mine" -> bot.gameMode.getGameModeForPlayer().isCreative() ? "attack once" : "attack continuous";
                    case "use" -> "use once";
                    case "turn_left" -> "turn left";
                    case "turn_right" -> "turn right";
                    case "look_down" -> "look down";
                    case "look_up" -> "look up";
                    case "back" -> "move backward";
                    case "sprint_forward" -> "move forward";
                    case "stop" -> "stop";
                    default -> "move " + action;
                };
                if (action.equals("sprint_forward")) runCarpet(source, "player " + BOT_NAME + " sprint");
                int applied = runCarpet(source, "player " + BOT_NAME + " " + command);
                boolean timed = List.of("forward", "back", "left", "right", "sprint_forward").contains(action)
                    || (action.equals("mine") && !bot.gameMode.getGameModeForPlayer().isCreative());
                stopAtTick = timed ? ticks + Math.max(1, durationMs / 50) : 0;
                return applied;
            }).get(3, TimeUnit.SECONDS);
            respond(exchange, result > 0 ? 200 : 409, Map.of("ok", result > 0));
        } catch (Exception e) { respond(exchange, 400, Map.of("error", e.toString())); }
    }

    private void handleReply(HttpExchange exchange) throws IOException {
        if (!exchange.getRequestMethod().equals("POST")) { respond(exchange, 405, Map.of("error", "POST required")); return; }
        try {
            JsonObject input = GSON.fromJson(new String(exchange.getRequestBody().readNBytes(1024), StandardCharsets.UTF_8), JsonObject.class);
            String text = input.get("text").getAsString().replace('\n', ' ').trim();
            if (text.isEmpty() || text.length() > 200) { respond(exchange, 400, Map.of("error", "invalid reply")); return; }
            server.submit(() -> server.getPlayerList().broadcastSystemMessage(Component.literal("[Craftmind] " + text), false)).get(3, TimeUnit.SECONDS);
            respond(exchange, 200, Map.of("ok", true));
        } catch (Exception e) { respond(exchange, 400, Map.of("error", e.toString())); }
    }

    private static void respond(HttpExchange exchange, int status, Object value) throws IOException {
        byte[] body = GSON.toJson(value).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        try (var out = exchange.getResponseBody()) { out.write(body); }
    }
}
