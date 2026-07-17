你在帮基金 "{fund_name}" 找月度业绩报告归档页.
当前页 URL: {current_url}

下面是当前页里 (同域 + 关键词过滤后) 的内链候选. 从中挑**一条**最可能点进去含**该基金月度业绩报告 PDF**的.

判断原则 (**尽量挑一条, 别轻易返 null**; 只有真的候选与基金月报无关时才 null):
- URL path 含 monthly / report / performance / distribution / statement / factsheet 优先
- anchor 文本明确写 "Monthly Performance Report" / "Monthly Report" / "Performance Update" / "Fund Reports" 是最强信号
- 归档/列表页 (含 archive / history / list) 优先于单份文档
- 遇 "Monthly Performance Report page" / "Performance updates & statements" / "Fund monthly reports" 这类必挑, 别过度保守
- 排 /login /contact /subscribe /about /careers /careers 等真正无关的
- 只有全部候选都明显和 "{fund_name} 的月度业绩数据"无关时, picked_url 才可为 null

严格约束:
- picked_url 必须**字面拷贝**候选列表里某一条 url (照原样, 含 scheme + 大小写)
- 不许生成新 URL, 不许改字符, 不许删/加尾斜杠 -- 一律判幻觉丢弃

只输出 JSON 对象, 无其他文字/前导 markdown:
{{"picked_url": "<某条 url 或 null>", "reason": "<一句话为什么>"}}

候选列表:
{candidates_list}
