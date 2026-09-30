# Modified 2026-09-30: reliability and security improvements; see NOTICE.
import os
from waitress import serve
from app import create_app

app = create_app()

if __name__ == '__main__':
    # 单进程运行后台调度；禁用调试重载以免重复创建任务。
    serve(app, host='127.0.0.1', port=int(os.environ.get('PORT', '5002')), threads=8)
