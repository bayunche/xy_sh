"""conversations 聚合：按买家分组 + 未读水位。"""
import time

from xy_gate.store import Store


def _mk(tmp_path):
    return Store(tmp_path / "t.sqlite3")


def test_conversations_group_by_buyer(tmp_path):
    st = _mk(tmp_path)
    st.add_message("c1", "m1", "in", "u1", "张三", "你好，还有货吗")
    st.add_message("c1", "m2", "out", "me", "机器人", "有的亲")
    st.add_message("c2", "m3", "in", "u2", "李四", "便宜点")
    convs = st.conversations()
    assert len(convs) == 2
    c1 = next(c for c in convs if c["chat_id"] == "c1")
    assert c1["buyer_name"] == "张三" and c1["buyer_id"] == "u1"
    assert c1["last_direction"] == "out" and c1["count"] == 2
    assert "便宜点" in next(c for c in convs if c["chat_id"] == "c2")["last_msg"]


def test_conversations_unread_and_mark_read(tmp_path):
    st = _mk(tmp_path)
    st.add_message("c1", "m1", "in", "u1", "张三", "第一条")
    time.sleep(0.01)
    st.mark_conversation_read("c1")
    time.sleep(0.01)
    st.add_message("c1", "m2", "in", "u1", "张三", "第二条")
    c1 = next(c for c in st.conversations() if c["chat_id"] == "c1")
    assert c1["unread"] == 1          # 已读水位之后又来一条
    st.mark_conversation_read("c1")
    c1 = next(c for c in st.conversations() if c["chat_id"] == "c1")
    assert c1["unread"] == 0


def test_conversations_empty_chat_excluded(tmp_path):
    st = _mk(tmp_path)
    st.add_message("", "m1", "in", "u1", "x", "无会话消息")
    st.add_message("c1", "m2", "in", "u1", "x", "正常")
    assert all(c["chat_id"] for c in st.conversations())
