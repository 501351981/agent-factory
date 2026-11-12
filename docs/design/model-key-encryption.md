# 设计：模型 API Key 落盘加密

> 状态：已实现 v1  
> 关联：Phase F / 工程加固

---

## 1. 问题

`ModelProfile.api_key` 曾以明文写入 SQLite。DB 文件泄露或备份外流即等于供应商密钥泄露。

目标：**落盘密文、运行时解密、管理台只见掩码；兼容存量明文并启动时迁移。**

---

## 2. 方案

- 算法：Fernet（AES-128-CBC + HMAC，`cryptography`）
- 主密钥：环境变量 `MODEL_SECRETS_KEY`（任意口令，内部 SHA-256 → Fernet key）
- 回退链：`MODEL_SECRETS_KEY` → `FACTORY_API_KEY` → `EMBED_TOKEN_SECRET` → 弱开发默认（打 warning）
- 存储格式：`enc:v1:<fernet-token>`；无此前缀视为遗留明文

| 时机 | 行为 |
|---|---|
| create / update（非空 key） | `seal_model_api_key` 后写入 |
| LLM /test /discover 用 key | `reveal_model_api_key` |
| list/get Out | 解密后做 mask；解密失败仍 `has_api_key=true`、mask=`****` |
| 进程启动 | `reseal_plaintext_model_api_keys` 把明文批次封存 |

API **从不**返回明文 key（保持 `api_key_masked`）。

---

## 3. 运维注意

- **生产必须设置 `MODEL_SECRETS_KEY`（或至少 `FACTORY_API_KEY`）**
- 更换 `MODEL_SECRETS_KEY` 后旧密文无法解密 → 管理台重新填写各模型 Key
- Agent 业务 AK/SK 仍是 hash（不可逆），与本方案无关

---

## 4. 验收

- [x] 新建模型 profile 后 DB 中为 `enc:v1:`，非明文
- [x] 对话 / test 仍能正常调上游
- [x] 列表只见 mask
- [x] 启动迁移遗留明文
- [x] 单测：`apps/server/tests/test_model_secrets.py`

### 自动化

```bash
uv run --project apps/server python -m unittest apps.server.tests.test_model_secrets -v
```
