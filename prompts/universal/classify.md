<!-- prompt_version: classify-v1.0 -->
# Codex 执行指导

执行任务快照中的 classify Workflow，只使用当前 Context Package 的 metadata、outline 和 selected blocks。
先读取 output.schema.json。完成判断后只将严格 JSON 对象写入指定 result.json，不加 Markdown 代码围栏。
身份、hash、版本、source_parse_quality 从 Context 原样回传，engine 使用 codex。
类别定义、证据规则和重试规则以 Workflow 为准，不在这里另建一套标准。
给用户可检查的短 rationale_summary 和 evidence note，不输出私有思维链。
若上下文不足，诚实降低 confidence 或选择 other，不读取未授权书籍、原始 Parser 输出或联网补充。
修改执行指导时必须更新 prompt_version。
