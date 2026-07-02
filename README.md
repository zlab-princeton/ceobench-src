<p align="center">
  <img src="assets/mascot.png" alt="CEO-Bench mascot" width="220"/>
</p>

<h1 align="center">CEO-Bench: Can Agents Play the Long Game?</h1>

<p align="center">
  <a href="https://tonychen.xyz/">Haozhe Chen</a>,
  <a href="https://www.cs.princeton.edu/~karthikn/">Karthik Narasimhan</a>,
  <a href="https://www.cs.princeton.edu/~zhuangl/">Zhuang Liu</a>
</p>

<p align="center">Princeton University</p>

<p align="center">
  <a href="https://ceobench.com">🌐 Website</a> &nbsp;|&nbsp;
  <a href="https://arxiv.org/pdf/2606.18543">📄 Paper</a> &nbsp;|&nbsp;
  <a href="https://ceobench.com/trajectory-viewer/">📊 Trajectory Viewer</a>
</p>



## 📊 Overview

<p align="center">
  <img src="assets/teaser.png" alt="CEO-Bench teaser" width="100%"/>
</p>

CEO-Bench evaluates general long-horizon agent capabilities by simulating a
startup over 500 days in a realistic and challenging environment. The agent
operates through a programmable interface with access to business databases,
company management tools, and social media. Outcomes are driven by a partially
observable, noisy, and evolving market with delayed and coupled consequences.




## 🚀 Running CEO-Bench

### 🔑 Setup: Environment variables

CEO-Bench has three LLM roles:

- the benchmarked agent model
- the social/macro post simulator model, Haiku 4.5 by default
- the enterprise customer simulator model, Sonnet 4.5 by default

Pick one provider family and use provider-specific model identifiers in
`src/saas_bench/config.py`.

**Option A: Amazon Bedrock for all models**

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-2"
```

```python
agent_llm_provider: str = "bedrock"
agent_llm_model: str = "anthropic.claude-fable-5"  # Claude Fable 5
# or another Bedrock model id, e.g. "us.anthropic.claude-sonnet-4-6"

social_post_llm_provider: str = "bedrock"
social_post_llm_model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

enterprise_llm_provider: str = "bedrock"
enterprise_llm_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
```

**Option B: Anthropic direct API for all models**

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

```python
agent_llm_provider: str = "anthropic"
agent_llm_model: str = "claude-fable-5"  # Claude Fable 5

social_post_llm_provider: str = "anthropic"
social_post_llm_model: str = "claude-haiku-4-5"

enterprise_llm_provider: str = "anthropic"
enterprise_llm_model: str = "claude-sonnet-4-5"
```

**Option C: OpenAI, OpenAI-compatible, or Azure OpenAI simulator models**

```bash
export OPENAI_API_KEY="..."

# Optional: OpenAI-compatible hosted endpoint.
export OPENAI_BASE_URL="https://your-endpoint.example/v1"
```

For Azure OpenAI, use Azure-specific environment variables instead:

```bash
export AZURE_OPENAI_API_KEY="..."
export AZURE_OPENAI_ENDPOINT="https://your-resource.openai.azure.com/"
export AZURE_OPENAI_API_VERSION="2025-04-01-preview"
```

```python
social_post_llm_provider: str = "openai"
social_post_llm_model: str = "your-social-model-or-azure-deployment"

enterprise_llm_provider: str = "openai"
enterprise_llm_model: str = "your-enterprise-model-or-azure-deployment"
```

For hosted OpenAI Responses-compatible endpoints that require custom query
params, headers, or a private CA bundle, configure non-secret transport
settings in `src/saas_bench/config.py` and keep secret values in environment.
These settings currently apply only when the simulator provider is `"openai"`;
chat-completions-only endpoints, including many local/open-source model servers,
are not supported by this path yet.

```python
simulator_llm_base_url: str = "https://your-endpoint.example/openai/v1"
simulator_llm_query_params: dict = {"api-version": "v1"}
simulator_llm_tls_cert_path: str = "/path/to/ca-bundle.crt"
simulator_llm_env_http_headers: dict = {"api-key": "AZURE_OPENAI_API_KEY"}
```

The same settings can be supplied without editing source through
`SIMULATOR_LLM_BASE_URL`, `SIMULATOR_LLM_QUERY_PARAMS` (JSON object),
`SIMULATOR_LLM_TLS_CERT_PATH`, and
`SIMULATOR_LLM_ENV_HTTP_HEADERS` (JSON object mapping header names to env var
names).

The LLM config fields are:

- `agent_llm_provider`, `agent_llm_model`, `agent_llm_reasoning_effort`
- `social_post_llm_provider`, `social_post_llm_model`
- `enterprise_llm_provider`, `enterprise_llm_model`

The bash-agent CLI `--provider`, `--model`, and `--reasoning-effort` flags only
override the benchmarked agent for ad hoc runs. Simulator social/macro and
enterprise LLMs use the simulator config and do not reuse the agent-only
`--api-key`. If you change a simulator provider, also set the corresponding
model to the identifier expected by that provider; model names are not
translated automatically.

For Claude Fable 5 agent runs, use `--provider anthropic --model claude-fable-5`
for the direct Anthropic API, or `--provider bedrock --model anthropic.claude-fable-5`
for Amazon Bedrock.


### 🎯 Option A: Evaluate any coding agent easily

We built CEO-Bench into a single executable and docs that any coding agent can just download the game and start playing.

The executable is hosted at **[zlab-princeton/run-ceobench](https://github.com/zlab-princeton/run-ceobench)**

If you want to evaluate a coding agent with terminal and internet access, prompt it

```
Download this, read instructions, and finish 500 day gameplay. https://github.com/zlab-princeton/run-ceobench
```



### ⚙️ Option B: Customize the configuration

All tunable simulator constants live in **`src/saas_bench/config.py`**: pricing,
customer groups, ad-channel productivity, R&D speed, competitor difficulty, etc.
After editing, rebuild the public bundle. 

```bash
uv sync                                  # one-time install
uv run python scripts/build_public.py    # rebuild public/ artifact
```

Then generated `public/` directory would play the same role as the same way as **[zlab-princeton/run-ceobench](https://github.com/zlab-princeton/run-ceobench)** in Option A

**Tuning difficulty** You can modify configuration in `config.py` to adjust difficulty.

An important difficulty is competitor strength. Competitor keeps track of a unreleased_dev_bank. Each agent's research and development quality improvement is added to this variable. At each competitor event, competitor draws `u ~ U(competitor_feedback_u_min, competitor_feedback_u_max)`, raises customer expectations by u × unreleased_dev_bank, and subtract this amount from unreleased_dev_bank. Larger competitor_feedback_u_min and competitor_feedback_u_max leads to stronger competitor and higher quality pressure. The default config value is (0.2,0.5). 



### 🤖 Option C: Replicate the bash-agent baseline

The paper's baseline gives an LLM a sandboxed bash shell plus the public CLI and
runs the full 500-day loop with checkpointing and logging. The full process:

**1. Install dependencies** (one-time):

```bash
uv sync
```

**2. Set provider credentials** in a `.env` file at the repo root. Which keys you
need depends on the agent model; for a Bedrock run:

```bash
AWS_ACCESS_KEY_ID="..."
AWS_SECRET_ACCESS_KEY="..."
AWS_REGION="us-east-2"
```

Other providers read `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
`XAI_API_KEY`, `TOGETHER_API_KEY`, or `MODAL_TOKEN_*`. No `NMDB_KEY` is needed:
the SQLCipher key is embedded in the engine.

If you configure simulator LLMs to use direct `anthropic` or `openai`, export
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in the shell before running. The
simulator does not receive the agent-only `--api-key`.

**3. Run.** `public/` ships prebuilt, so there is no build step:

```bash
uv run python -m saas_bench.agents.bash_agent.run_test \
    --model us.anthropic.claude-sonnet-4-6 \
    --provider bedrock \
    --reasoning-effort max \
    --seed 42 \
    --days 500 \
    --workspace bash_agent_runs
```

**4. Output.** Each run lands at `bash_agent_runs/run_<id>/`: `world.nmdb`
(encrypted ledger), `config.json`, `checkpoint.json`, `agent_workspace/` (the
agent's sandbox, a fresh git repo with weekly commits), and `logs/` containing
per-turn `raw_responses_<id>.jsonl` (model thinking + tool calls),
`tool_results_<id>.jsonl` (tool calls + their outputs), and
`timing_<id>.jsonl`. To score and analyze the run, see
[docs/analyze_trajectory.md](docs/analyze_trajectory.md).

If you edit `src/saas_bench/config.py`, rebuild the bundle the agent sees with
`uv run python scripts/build_public.py` before launching.



## 📈 Analyzing agent trajectory

Every finished run leaves a single artifact: an encrypted `world.nmdb` ledger
(SQLCipher, page-level AES-256). It is the complete record of the run: cash,
subscriptions, customers, competitor events, and every action the agent took.

The decryption key is fixed and bundled into the published `novamind-operation`
zipapp at build time; see `KEYS.md` in this repo for the value, or import it
from the compiled `saas_bench._embedded_key` module. To decrypt and query:

```bash
KEY=$(grep _NMDB_KEY KEYS.md | head -1 | cut -d'"' -f2)
sqlcipher path/to/world.nmdb \
  "PRAGMA key = '$KEY';" \
  "SELECT day, category, amount FROM ledger ORDER BY day, id LIMIT 10;"
```

For the database schema, analysis recipes, and notes on keeping the agent from
cheating, see **[docs/analyze_trajectory.md](docs/analyze_trajectory.md)**.



## 📁 Repo layout

```
ceobench-src/
├── README.md                          ← this file
├── docs/
│   └── analyze_trajectory.md          ← decrypt, schema + analysis guide
├── public_sources/                    ← human-written inputs to the public build
│   ├── README.md, requirements.txt
│   └── examples/{autoplay_loop,basic_strategy}.py
├── scripts/
│   ├── build_public.py                ← canonical public-repo builder
│   ├── start_fresh_sonnet_bash.sh     ← bash-agent launcher (Bedrock Sonnet)
│   ├── start_fresh_gpt_bash.sh        ← bash-agent launcher (OpenAI GPT)
│   └── resume_run.sh                  ← resume bash agent from checkpoint
└── src/saas_bench/                    ← simulator + bash agent
    ├── simulation.py, environment.py, shocks.py, event_logger.py
    ├── config.py                      ← all tunable constants
    ├── customer_llm.py, personas.py, enterprise.py
    ├── database.py, db_protection.py
    ├── api_server.py, server_entry.py, tools.py
    ├── novamind_api/, novamind_cli.py, _public_cli.py
    └── agents/bash_agent/             ← canonical baseline harness
```



## 📜 Citation

```bibtex
@misc{chen2026ceobenchagentsplaylong,
  title={CEO-Bench: Can Agents Play the Long Game?},
  author={Haozhe Chen and Karthik Narasimhan and Zhuang Liu},
  year={2026},
  eprint={2606.18543},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
  url={https://arxiv.org/abs/2606.18543},
}
```
