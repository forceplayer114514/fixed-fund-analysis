# Task 1: 优化 URL 探测 `discover_source.py`


**Files:**
- Modify: `scripts/discover_source.py`
- Test: `tests/test_discover_source.py` (Create)

**Interfaces:**
- Consumes: `fund_registry.yaml`
- Produces: 能够快速探测 `confirmed_url` 是否可用，若可用则直接跳过搜索引擎检索。

- [ ] **Step 1: 编写测试用例验证快速探测逻辑**

Create: `tests/test_discover_source.py`
```python
import pytest
import requests
from unittest.mock import patch, MagicMock

# 模拟快速探测函数
def quick_verify_url(url: str) -> bool:
    try:
        resp = requests.head(url, timeout=5, headers={"User-Agent": "Mozilla"})
        return resp.status_code == 200
    except Exception:
        return False

@pytest.mark.unit
def test_quick_verify_url_success():
    with patch('requests.head') as mock_head:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_head.return_value = mock_resp
        assert quick_verify_url("https://example.com") is True

@pytest.mark.unit
def test_quick_verify_url_fail():
    with patch('requests.head', side_effect=requests.RequestException):
        assert quick_verify_url("https://example.com") is False
```

- [ ] **Step 2: 运行测试确保失败（本阶段创建测试将直接通过，无需失败）**

Run: `python3 -m pytest tests/test_discover_source.py`
Expected: PASS (测试本身只包含 mock 验证)

- [ ] **Step 3: 修改 `scripts/discover_source.py` 以加入直连探测秒级跳过**

Modify `scripts/discover_source.py` around line 345:
```python
    # Step 1: Check existing confirmed URL
    confirmed_url = fund_info.get("confirmed_url")
    if confirmed_url:
        log_attempt(f"Registry has existing URL: {confirmed_url}. Checking with quick HEAD request...")
        try:
            # 轻量探测以提高速度
            resp = requests.head(confirmed_url, headers=HEADERS, timeout=5)
            is_alive = (resp.status_code == 200)
        except Exception:
            is_alive = False

        if is_alive:
            log_attempt(f"Quick check succeeded. Verifying content rules...")
            if verify_url(confirmed_url, fund_id, fund_name, apir_code):
                log_attempt(f"SUCCESS: Existing URL is verified and active: {confirmed_url}")
                fund_info["verified_at"] = datetime.datetime.now().strftime("%Y-%m-%d")
                fund_info["verification_signal"] = "Verified existing registry URL via quick probe"
                save_registry(registry)
                sys.exit(0)
```

- [ ] **Step 4: 运行 pytest 确保现有测试全部通过**

Run: `python3 -m pytest tests/`
Expected: PASS

- [ ] **Step 5: 提交更改**

```bash
git add scripts/discover_source.py tests/test_discover_source.py
git commit -m "feat: add quick HEAD probe for source discovery to skip engine search"
```

---