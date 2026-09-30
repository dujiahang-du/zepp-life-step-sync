# StepSync · Zepp Life 步数同步管理

Flask + SQLite 的 Zepp Life 账号管理工作台，提供响应式页面、按小时计划、执行状态与记录统计。所有时间按北京时间显示。

## 本机运行

建议使用 Python 3.12（本项目验收环境），需要 Python 3.11 或更新版本。

```powershell
git clone https://github.com/dujiahang-du/zepp-life-step-sync.git
cd zepp-life-step-sync
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Windows 也可在安装依赖后双击 `start.cmd`。打开 <http://127.0.0.1:5002>，注册网站账号，然后添加自己的 Zepp Life 手机号或邮箱账号。网站账号和运动账号是两套独立凭据。

使用 Waitress 单进程运行，默认仅监听本机，关闭进程或电脑休眠期间不会执行计划。不要开启开发重载器或同时运行多份服务。文件锁用于避免同一实例重复启动调度，不是分布式部署方案。

## 功能与交互

- 桌面侧栏、手机导航、空状态引导；所有样式、图标、脚本本地加载。
- 添加账号只验证认证，不提交测试步数；编辑时密码留空可保留原密码。
- 步数范围 0–98,800，最大值必须大于 0；执行时间 0–23 点，不支持跨午夜计划。
- 启用计划后在起止时间内每小时整点执行；步数范围按当前时间递增，同一天不低于本系统已成功提交的最高值。
- 手动提交进入后台队列，页面显示等待、执行中和结果；同一账号防重复执行，30 秒内限制再次提交。
- 账号暂停、编辑和删除；删除前有确认框，删除账号同时移除其记录。
- 按账号显示七日趋势和真实成功比例；记录可按日期、状态筛选和分页。

“已接受”表示 Zepp 接口返回成功，不代表微信或支付宝已经更新。不同账号的授权、绑定关系和第三方协议变化，需要在对应 App 中核对。

## 数据与安全

运行时数据在 `instance/`，不属于公开源码：

- `mimotion.db`：网站用户、加密运动凭据、执行任务及记录。
- `secrets.json`：首次运行生成的随机会话密钥和 Fernet 加密密钥。
- `logs/`、`backups/`：运行日志及升级前 SQLite 快照。

网站密码使用哈希存储；运动密码和令牌使用本机随机密钥加密。数据库与密钥必须一起备份，且仅允许可信系统用户读取。密钥丢失会导致已有运动凭据无法解密，不能靠重新生成密钥恢复。旧版明文数据库备份仍可能含敏感信息，应留在本机受控目录。

默认启用 CSRF、防跨用户访问、请求体限制、安全响应头和登录限流。注册默认开放给可访问本机页面的人。若另行部署到网络，需要 HTTPS、反向代理访问控制及适当的运维配置；仓库公开不会自动公开本机服务。

可选环境变量：

| 变量 | 作用 |
| --- | --- |
| `PORT` | 本机端口，默认 5002 |
| `DATABASE_URL` | 数据库连接地址，默认实例目录 SQLite |
| `SECRET_KEY` | 覆盖会话密钥，必须为私密随机值 |
| `ENCRYPTION_KEY` | 覆盖 Fernet 密钥；已有数据须使用原密钥 |
| `SCHEDULER_ENABLED=0` | 关闭自动计划 |
| `ALLOW_REGISTRATION=0` | 关闭网站注册 |
| `COOKIE_SECURE=1` | 仅通过 HTTPS 发送会话 Cookie |

已有 SQLite 数据库启动时增量升级；涉及字段或密码迁移前自动备份。发现重复账号或密钥错误会停止启动。其他数据库的既有表不支持自动迁移。

回退时先停止服务，再恢复升级前源码和匹配的数据库快照；保留当前实例的独立副本。不要只替换数据库而丢失对应加密密钥。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pip check
```

自动测试禁止真实外部网络，覆盖认证、CSRF、权限隔离、加密迁移、调度、异步执行与重复保护、协议构造、异常恢复、统计、分页和删除关联记录。

桌面及移动端已验收登录、账号表单、模拟成功/失败、编辑、删除取消和记录筛选。真实 Zepp 登录、远端提交及微信展示不属于模拟测试可保证的范围。

## 来源与许可

基于 [CoderXiaopang/mimotion-web](https://github.com/CoderXiaopang/mimotion-web) 的 Flask 项目改造。Zepp 协议参考 [TonyJiangWJ/mimotion](https://github.com/TonyJiangWJ/mimotion)，核对版本 `7bbac81`。

保留 Apache-2.0 [LICENSE](LICENSE)。2026-09-30 的改造范围：认证协议、调度、凭据保护、参数校验、统计、页面与交互、测试和运行配置。详见 [NOTICE](NOTICE)。请按平台规则使用自己的账号。
