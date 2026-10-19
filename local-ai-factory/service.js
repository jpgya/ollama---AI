const {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const LABEL = "com.localaifactory.agent";
const root = __dirname;
const plistPath = path.join(os.homedir(), "Library", "LaunchAgents", `${LABEL}.plist`);
const configPath = path.join(root, "config.json");
const domain = typeof process.getuid === "function" ? `gui/${process.getuid()}` : null;
const target = domain ? `${domain}/${LABEL}` : null;

function xml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function runLaunchctl(args) {
  const result = spawnSync("/bin/launchctl", args, {
    encoding: "utf8",
    env: {
      HOME: os.homedir(),
      PATH: [process.env.PATH, "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        .filter(Boolean)
        .join(":"),
    },
  });
  if (result.error) throw result.error;
  return result;
}

function isLoaded() {
  const result = runLaunchctl(["print", target]);
  if (result.status === 0) return true;
  if (/could not find service|service not found|no such process/i.test(result.stderr || "")) {
    return false;
  }
  throw new Error(`launchctlで登録状態を確認できません: ${(result.stderr || result.stdout).trim()}`);
}

function plistContents(logDirectory) {
  const nodePath = process.execPath;
  const serverPath = path.join(root, "server.js");
  const argumentsXml = [nodePath, serverPath, "--config", configPath]
    .map((argument) => `    <string>${xml(argument)}</string>`)
    .join("\n");
  const environmentPath = [
    process.env.PATH,
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
  ].filter(Boolean).join(":");
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
${argumentsXml}
  </array>
  <key>WorkingDirectory</key>
  <string>${xml(root)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>30</integer>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${xml(os.homedir())}</string>
    <key>PATH</key>
    <string>${xml(environmentPath)}</string>
    <key>LOCAL_AI_FACTORY_OPEN_BROWSER</key>
    <string>0</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${xml(path.join(logDirectory, "launch-agent.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>${xml(path.join(logDirectory, "launch-agent.err.log"))}</string>
</dict>
</plist>
`;
}

function configuredLogDirectory() {
  const sourcePath = existsSync(configPath)
    ? configPath
    : path.join(root, "config.example.json");
  let config;
  try {
    config = JSON.parse(readFileSync(sourcePath, "utf8"));
  } catch (error) {
    throw new Error(`設定ファイルを読み込めません (${sourcePath}): ${error.message}`);
  }
  if (!config || typeof config.data_root !== "string" || !config.data_root.trim()) {
    throw new Error(`設定の data_root が不正です: ${sourcePath}`);
  }
  return path.resolve(root, config.data_root, "logs");
}

function requireMacOS() {
  if (process.platform !== "darwin" || !domain) {
    throw new Error("LaunchAgentの登録・解除はログイン中のmacOSユーザーで実行してください。");
  }
}

function install() {
  requireMacOS();
  const launchAgents = path.dirname(plistPath);
  const logDirectory = configuredLogDirectory();
  mkdirSync(launchAgents, { recursive: true });
  mkdirSync(logDirectory, { recursive: true });

  if (isLoaded()) {
    const unloaded = runLaunchctl(["bootout", target]);
    if (unloaded.status !== 0) {
      throw new Error(`既存の工場サービスを停止できません: ${unloaded.stderr.trim()}`);
    }
  }

  const temporaryPath = `${plistPath}.${process.pid}.tmp`;
  writeFileSync(temporaryPath, plistContents(logDirectory), { mode: 0o600 });
  renameSync(temporaryPath, plistPath);
  const loaded = runLaunchctl(["bootstrap", domain, plistPath]);
  if (loaded.status !== 0) {
    throw new Error(`工場サービスを登録できません: ${(loaded.stderr || loaded.stdout).trim()}`);
  }
  console.log(`工場サービスを登録し、起動しました: ${plistPath}`);
  console.log(`状態: launchctl print ${target}`);
  console.log("ログイン時に自動起動します。停止するには npm run service:uninstall を実行してください。");
}

function uninstall() {
  requireMacOS();
  if (isLoaded()) {
    const unloaded = runLaunchctl(["bootout", target]);
    if (unloaded.status !== 0) {
      throw new Error(`工場サービスを停止できません: ${(unloaded.stderr || unloaded.stdout).trim()}`);
    }
  }
  if (existsSync(plistPath)) unlinkSync(plistPath);
  console.log("工場サービスを解除しました。保存済みの成果物とログは削除していません。");
}

const command = process.argv[2];
try {
  if (command === "install") install();
  else if (command === "uninstall") uninstall();
  else {
    console.error("使い方: node service.js install|uninstall");
    process.exitCode = 2;
  }
} catch (error) {
  console.error(`工場サービスの操作に失敗しました: ${error.message}`);
  process.exitCode = 1;
}
