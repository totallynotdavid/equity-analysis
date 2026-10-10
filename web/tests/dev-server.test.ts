import { type ChildProcess, spawn } from "node:child_process";
import { connect, createServer } from "node:net";
import { networkInterfaces } from "node:os";
import { afterAll, beforeAll, expect, it } from "vitest";

const external = Object.values(networkInterfaces())
  .flat()
  .find((address) => address && !address.internal && address.family === "IPv4");

function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const server = createServer();
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address() as { port: number };
      server.close(() => resolve(port));
    });
  });
}

// A firewall may drop the packets to a host's own network address instead of
// refusing them, so a connection that stays silent counts as unreachable.
function reachable(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host, port, timeout: 1_000 });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

let server: ChildProcess;
let output = "";
let port: number;

beforeAll(async () => {
  port = await freePort();
  // The server gets its own process group so afterAll can stop astro as well
  // as bun. Without --ignore-lock, astro hands the server to a background
  // process or refuses to start beside a dev server the developer already runs.
  server = spawn(
    "bun",
    ["run", "dev", "--port", String(port), "--ignore-lock"],
    {
      detached: true,
    },
  );
  server.stdout?.on("data", (chunk) => (output += chunk));
  server.stderr?.on("data", (chunk) => (output += chunk));
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await reachable("localhost", port)) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`the dev server did not start:\n${output}`);
}, 30_000);

afterAll(() => {
  if (server.pid) process.kill(-server.pid);
});

it("listens on localhost", async () => {
  expect(await reachable("localhost", port)).toBe(true);
});

it.skipIf(!external)("does not listen on a network address", async () => {
  expect(await reachable(external!.address, port)).toBe(false);
});
