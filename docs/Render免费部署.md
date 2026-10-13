# Render免费托管

2026-10-13，用户已同意将网页/程序托管到Render Free，并将百炼/Tavily密钥保存到自己Render服务的私密环境变量。首先只上线网页，不发起模型或搜索调用；本文件和render.yaml不包含实际密钥。

代码入口 `python -m app.cloud_main` 已监听0.0.0.0并读取平台PORT。`pip install .`只安装setup.py列出的运行依赖。健康检查 `/health`。免费套餐需显式选择Free，不能省略plan；目前免费Python服务为0.1CPU、512MB。实例规格与可用地区以实际创建页为准。

render.yaml采用官方Blueprint配置：plan=free、region=singapore、autoDeployTrigger=off、截止时间为空；私密变量通过sync:false在平台输入，签名秘密由平台生成。该配置未在Render部署或验证。

## 验收分两步

1. 先确认公共HTTPS页面、输入框、健康状态和访客隔离；/api/config应当显示cloud_online=true、online_enabled=false，真实调用被拒绝。不要把此步当作真实搜索报告已完成。
2. 另获明确预算后才设置固定结束时间，验证一个完整有来源案例。不得自动续期，或把免费托管理解成模型/搜索免费。

Free服务闲置15分钟后休眠，唤醒约一分钟；重启/休眠清空临时SQLite，记录和任务计数会丢失。当前方案只适合限时小范围试用，持续开放前需要持久化额度及记录。免费Render Postgres30天到期，不作为长期存储。

官方资料：[Blueprint字段](https://render.com/docs/blueprint-spec)、[免费限制](https://render.com/docs/free)、[Web Services](https://render.com/docs/web-services)。
