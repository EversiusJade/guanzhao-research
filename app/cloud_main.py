"""百炼代码包入口：联网模式必须配置密钥、访客签名及固定结束时间。"""
import os
import uvicorn


def run_app():
    os.environ.setdefault('GUANZHAO_DEPLOYMENT','cloud-online')
    uvicorn.run('app.main:app', host='0.0.0.0', port=int(os.getenv('PORT', '8080')),
                workers=1, proxy_headers=True)


if __name__ == '__main__':
    run_app()
