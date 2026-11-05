// Use the SDK callback to save a scannable local PNG instead of terminal QR art.
import { pathToFileURL } from 'node:url';
import fs from 'node:fs/promises';
import path from 'node:path';

const [runtime, account, directory] = process.argv.slice(2);
const { AccountManager } = await import(pathToFileURL(path.join(runtime, 'npm/node_modules/weread-omni/dist/index.js')));
const { default: QRCode } = await import(pathToFileURL(path.join(runtime, 'npm/node_modules/qrcode/lib/index.js')));
const emit = data => process.stdout.write(JSON.stringify(data) + '\n');
await fs.mkdir(directory, { recursive: true, mode: 0o700 });
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 300000);
const png = path.join(directory, `api-login-${Date.now()}.png`);
try {
  const result = await new AccountManager().login(account, {
    signal: controller.signal,
    onQr: async (url, stage) => {
      await QRCode.toFile(png, url, { width: 480, margin: 4 });
      await fs.chmod(png, 0o600);
      emit({ event: 'scan_required', stage, qr_image: png, expires_in_seconds: 300 });
    },
    onStatus: async status => emit({ event: 'login_status', status }),
  });
  emit({ ok: true, account: result.account, vid: result.vid });
} catch (error) {
  emit({ ok: false, code: controller.signal.aborted ? 'LOGIN_TIMEOUT' : error?.name === 'TransportError' ? 'NETWORK_ERROR' : 'LOGIN_FAILED',
         message: 'API 登录未完成。二维码过期可重新运行；人工验证须在官方客户端处理。' });
  process.exitCode = 1;
} finally {
  clearTimeout(timer);
  await fs.rm(png, { force: true });
}
