# FastAPI 入门

FastAPI 是基于 Python 类型注解的现代 Web 框架，底层使用 Starlette 处理 ASGI 协议，使用 Pydantic 做数据校验和序列化。它的特点是开发效率高、自动生成接口文档、原生支持异步。

## 路由与装饰器

用 app.get、app.post、app.delete 这类装饰器把函数注册为路由。函数名任意，路径和 HTTP 方法由装饰器决定。路径参数写在花括号里，例如 /api/docs/{doc_id}，函数参数名与花括号中的名字一致即可自动注入。查询参数直接声明为函数参数，带默认值的就是可选参数。

## Pydantic 请求模型

请求体推荐用 Pydantic 的 BaseModel 定义，字段类型、范围校验、必填与可选都可以通过 Field 声明。FastAPI 会在进入处理函数之前完成校验，校验失败自动返回 422 状态码并给出字段级错误信息。这样处理函数里就不需要写大量的 if 判断。

## 异步处理

FastAPI 支持同步函数和异步函数两种路由。声明为 async def 的函数在事件循环中执行，适合等待网络或数据库 IO；普通的 def 函数会被放到线程池执行，适合 CPU 计算或阻塞式数据库调用。注意不要在 async 函数里做阻塞式 IO，否则会卡住整个事件循环。

## 静态文件与启动

用 StaticFiles 挂载静态目录，用 FileResponse 返回单页 HTML，就可以在不使用 npm 构建的情况下提供前端页面。开发时用 uvicorn app.main:app --reload 启动，生产环境去掉 reload，并指定 host 和 port。启动和关闭需要执行的逻辑（例如初始化数据库、启动后台线程）可以放在 lifespan 上下文管理器里。

## 返回 JSON

处理函数直接返回 dict，FastAPI 会自动序列化成 JSON。需要自定义状态码时在装饰器里指定 status_code，或者返回 JSONResponse。错误处理可以抛出 HTTPException，也可以注册全局异常处理器统一返回格式。
