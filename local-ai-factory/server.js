const { spawn } = require("node:child_process");
const { existsSync } = require("node:fs");
const path = require("node:path");

const localPython = path.join(__dirname, ".venv", "bin", "python");
const python = process.env.LOCAL_AI_FACTORY_PYTHON
  || (existsSync(localPython) ? localPython : "python3");
const child = spawn(
  python,
  ["-m", "local_ai_factory.web", ...process.argv.slice(2)],
  { cwd: __dirname, stdio: "inherit" },
);
let stopping = false;

function stop(signal) {
  if (stopping) return;
  stopping = true;
  if (child.exitCode === null) child.kill(signal);
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));
child.on("error", (error) => {
  console.error(`Pythonを起動できませんでした (${python}): ${error.message}`);
  process.exitCode = 1;
});
child.on("exit", (code, signal) => {
  if (!stopping) process.exitCode = code ?? (signal ? 1 : 0);
});
