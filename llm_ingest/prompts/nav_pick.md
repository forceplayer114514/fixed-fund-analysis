你在帮基金 "{fund_name}" 找月度业绩报告归档页.
当前页 URL: {current_url}

下面是当前页里 (同域 + 关键词过滤后) 的内链候选. 挑一条**最可能点进去有多份月报 PDF** 的.

判断原则:
- URL path 含 monthly / report / performance / distribution / statement / factsheet 优先
- anchor 文本明确写 "Monthly Report" / "Performance Update" / "Monthly Performance Report" / "Fund Reports" 最高
- 归档/列表页 (含 archive / history / list) 优先于单份文档
- 排 /login /contact /subscribe /about /careers 等无关链接
- 若无一条合适, picked_url 必须为 null (禁止硬凑)

严格约束:
- picked_url 必须**字面拷贝**候选列表里的某一条 url (照原样, 含 scheme + 大小写). 生成新 URL 会被判为幻觉直接丢弃.

只输出 JSON 对象, 无其他文字/前导 markdown:
{{"picked_url": "<某条 url 或 null>", "reason": "<一句话为什么>"}}

候选列表:
{candidates_list}
