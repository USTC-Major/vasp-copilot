"""上下文构造回归：当前消息只进一次、产品名、按可用性裁剪工具、截断不静默。"""
from types import SimpleNamespace

import pytest

from ai_mode.agent.runner import MESSAGE_CHAR_LIMIT, _final_answer, build_messages
from ai_mode.agent.tool_schema import tool_schema_text
from ai_mode.config import AiModeConfig
from backend.tests.toolbox.legacy_bridge import ProjectStore

GATED_TOOLS = ("mp_search", "mp_import_poscar", "hpc_list", "hpc_read", "hpc_upload")


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    pid = store.create_project("上下文回归")["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    tid = store.create_task(pid, goal="上下文回归", local_workspace=str(root))["id"]
    return SimpleNamespace(store=store, pid=pid, tid=tid, root=root)


def build(ctx, history, content, cfg=None):
    task = ctx.store.get_task(ctx.pid, ctx.tid)
    return build_messages(ctx.store, task, history, content, cfg=cfg)


def user_texts(messages):
    return [m["content"] for m in messages if m["role"] == "user"]


def test_current_message_enters_context_once(ctx):
    """两个入口都会先把当前用户消息落库，历史已含本条，不能再追加一次。"""
    history = [{"role": "user", "content": "你好"},
               {"role": "assistant", "content": "在的"},
               {"role": "user", "content": "帮我看看工作区"}]  # 当前轮，已落库
    messages = build(ctx, history, "帮我看看工作区")
    assert user_texts(messages).count("帮我看看工作区") == 1
    assert messages[-1] == {"role": "user", "content": "帮我看看工作区"}


def test_user_repeating_the_same_text_is_preserved(ctx):
    """用户主动连发两次相同内容时，两遍都要保留，不能按文本去重。"""
    history = [{"role": "user", "content": "再算一次"},
               {"role": "assistant", "content": "好的"},
               {"role": "user", "content": "再算一次"}]
    messages = build(ctx, history, "再算一次")
    assert user_texts(messages).count("再算一次") == 2


def test_history_without_current_message_still_appended(ctx):
    """历史里没有当前消息（内部调用）时，仍然要追加当前消息。"""
    messages = build(ctx, [{"role": "assistant", "content": "在的"}], "新问题")
    assert user_texts(messages) == ["新问题"]


def test_system_prompt_uses_current_product_name(ctx):
    system = build(ctx, [], "你好")[0]["content"]
    assert "VASP-Copilot" in system
    assert "VASP-Doctor" not in system


def test_system_prompt_forbids_engineering_jargon(ctx):
    """面向用户必须说人话：禁止哈希/内部编号/实现术语，并给出反例与正例。"""
    system = build(ctx, [], "你好")[0]["content"]
    assert "面向用户的说法" in system
    assert "已原子写入" in system          # 反例留在提示词里，明确禁止
    assert "SHA-256" in system             # 反例里出现，但被标为禁止
    assert "mp-xxxxxxx" in system          # 内部编号同样禁止
    assert "Si 的金刚石结构" in system      # 正例示范


def test_tools_are_trimmed_when_hpc_and_mp_are_unconfigured(ctx):
    ready = AiModeConfig(mp_api_key="test-key", ssh_host="host", ssh_username="user")
    full = build(ctx, [], "你好", cfg=ready)[0]["content"]
    for name in GATED_TOOLS:
        assert f"- {name}：" in full

    bare = build(ctx, [], "你好", cfg=AiModeConfig())[0]["content"]
    for name in GATED_TOOLS:
        assert f"- {name}：" not in bare
    assert "尚未配置 Materials Project" in bare
    assert "尚未配置超算" in bare
    # 本地工具不受影响
    for name in ("get_state", "ws_list", "plan", "precheck", "submit"):
        assert f"- {name}：" in bare


def test_tool_schema_default_stays_complete():
    """默认参数保持完整清单，供未传配置的调用方与测试复用。"""
    text = tool_schema_text()
    for name in GATED_TOOLS:
        assert f"- {name}：" in text


def test_long_current_message_is_marked_not_silently_cut(ctx):
    long_text = "约" * (MESSAGE_CHAR_LIMIT + 1500)
    last = build(ctx, [], long_text)[-1]["content"]
    assert last.startswith("约" * 10)
    assert f"已截断 1500 字符" in last
    assert len(last) < MESSAGE_CHAR_LIMIT + 200


def test_long_history_entry_is_marked(ctx):
    long_text = "x" * (MESSAGE_CHAR_LIMIT + 50)
    messages = build(ctx, [{"role": "assistant", "content": long_text}], "继续")
    history_text = next(m["content"] for m in messages if m["role"] == "assistant")
    assert "已截断 50 字符" in history_text


def test_short_message_is_not_touched(ctx):
    assert build(ctx, [], "你好")[-1]["content"] == "你好"


# --- 最终回答的取段规则（避免同一句话被答两三遍 / 混入调用前叙述） ---

def test_final_answer_prefers_prose_after_last_tool():
    assert _final_answer(["调用前的叙述", "工具后的总结"], ["工具后的总结"]) == "工具后的总结"


def test_final_answer_falls_back_to_all_prose():
    assert _final_answer(["只有这一段"], []) == "只有这一段"


def test_final_answer_ignores_blank_tail():
    assert _final_answer(["正文"], ["   "]) == "正文"
