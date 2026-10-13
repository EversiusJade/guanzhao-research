from setuptools import setup

setup(
    name='guanzhao-research', version='0.3.1',
    description='Guanzhao multi-perspective research assistant with bounded cloud access',
    packages=['app','fixtures','deploy_starter'],
    package_data={'app':['static/*.html','static/*.css','static/*.js','skills/*.md','skills/references/*.md'],
                  'fixtures':['demo.json']},
    python_requires='>=3.11',
    install_requires=['fastapi==0.143.0','httpx==0.28.1','pydantic==2.14.0',
                      'python-dotenv==1.2.4','uvicorn==0.54.0'],
    entry_points={'console_scripts':['guanzhao-research=app.cloud_main:run_app']},
)
