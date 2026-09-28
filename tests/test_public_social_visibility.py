"""Exercise actual social processing: private judge output stays private."""
import builtins
import json
from types import SimpleNamespace

from saas_bench.public_query import public_query_boundary
from saas_bench.tools import AgentTools


def test_judged_social_post_exposes_only_public_feedback(monkeypatch, make_initialized_sim):
    conn, sim, config = make_initialized_sim(seed=42)
    sim.customer_simulator = SimpleNamespace(social_post_client=object(), _log_cost=lambda *a, **k: None)
    conn.execute("INSERT INTO agent_social_media_posts(day,content) VALUES (0,'A public product update')")
    monkeypatch.setattr('saas_bench.customer_llm.judge_agent_social_post',
                        lambda *a, **k: (.85, 'PRIVATE JUDGE REASONING', 1, 1))
    monkeypatch.setattr('saas_bench.customer_llm.generate_customer_reply_to_agent',
                        lambda *a, **k: ('A public customer response', 1, 1))
    original_open = builtins.open
    def safe_open(path, *a, **k):
        assert 'social_reply_debug' not in str(path), 'Judge feedback must not be written into agent-readable debug files'
        return original_open(path, *a, **k)
    monkeypatch.setattr(builtins, 'open', safe_open)
    sim.current_day = 1
    sim._process_agent_social_posts(sim.get_current_config())
    private = conn.execute('SELECT effect_by_group, reasoning_by_group, views, comment_post_ids FROM agent_social_media_posts').fetchone()
    assert .85 in json.loads(private['effect_by_group']).values()
    assert 'PRIVATE JUDGE REASONING' in private['reasoning_by_group']
    assert private['views'] > 0
    assert json.loads(private['comment_post_ids'])
    notification = conn.execute("SELECT message FROM notifications WHERE type='social_media'").fetchone()[0]
    assert 'views' in notification
    assert 'Top reactions' not in notification
    assert 'PRIVATE JUDGE' not in notification
    assert '+0.8' not in notification and '+0.9' not in notification
    assert 'positively' not in notification
    public = AgentTools.get_social_posts(SimpleNamespace(conn=conn)).data
    assert public['posts']
    for post in public['posts']:
        assert post['content'] == 'A public customer response'
        assert post['reply_to_agent_post_id'] == 1
        assert post['replying_to_your_post'] == 'A public product update'
        assert not {'source_group_id','sentiment','customer_id'} & post.keys()
    with public_query_boundary(conn):
        cols = {c[0] for c in conn.execute('SELECT * FROM agent_social_media_posts').description}
        assert not {'effect_by_group','reasoning_by_group','views_by_group'} & cols
    conn.close()
