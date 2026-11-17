# NovaMind Tools Reference

Complete reference for all available tools. Call them via the Python API
(`import novamind_api as nm`) — see `docs/novamind_api/` for the SDK source
and run scripts with `./novamind-operation python <script.py>` or
`./novamind-operation python-c "<inline code>"`.

## Tool Summary

| Tool | Description |
|------|-------------|
| `set_prices` | Set monthly subscription prices for plans A, B, and C. |
| `set_model_tiers` | Set AI model tiers for plans A, B, and C. Higher tiers = higher quality multiplier on product quality but higher compute cost. |
| `set_daily_spend` | Set daily spending for operations and development. Advertising spend is set via set_targeted_ad_spend (per channel × group) only. |
| `set_targeted_ad_spend` | Set per-(channel, group) ad spend. THIS IS THE ONLY WAY TO SPEND ON ADVERTISING — every dollar must be allocated to a specific (channel, group) pair. |
| `set_targeted_ops_spend` | Set ADDITIONAL operations spending targeted at specific scopes (group, plan, group+plan, individual customer). Each scope runs its own independent Poisson resolution pool on top of the global ops pool. |
| `set_targeted_dev_spend` | Set ADDITIONAL per-group development spending on top of the global dev spend. Provides a CUMULATIVE per-group quality bonus that grows daily while spending continues. Investment persists even after spending stops. |
| `set_capacity_tier` | Set infrastructure capacity tier. Higher tiers handle more usage but cost more per day. |
| `set_usage_quotas` | Set daily usage quotas (rate limits) per seat for each plan. Exceeding quota degrades experience. |
| `send_enterprise_deal` | Send enterprise deal offerings. Compact tuple format: each deal = [customer_id, [[plan, price_per_seat], ...]]. All contracts are month-to-month (1 month). If the customer has an open negotiation thread, replies to it. If no open thread, initiates renegotiation. Up to 3 offerings per deal. Customer picks the best. Late replies damage relationship (-0.02/day after 1 day grace). No response within 3 days = customer LOST FOREVER. |
| `python_exec` | Execute Python code for custom data analysis. Has read-only access to the full simulation database. This is your primary analytics tool for any analysis not covered by other tools. |
| `register_daily_calculation` | Register a named calculation to run automatically at the start of each day. Output appears in dashboard. |
| `remove_daily_calculation` | Remove a registered daily calculation. |
| `list_daily_calculations` | List all registered daily calculations. |
| `get_social_posts` | Search social media posts about your company. NOTE: Sentiment is NOT provided - you must infer it from the post content. |
| `post_social_media` | Post a social media message on company social media account. You can either post an original post or reply to an existing customer. Max 280 characters. Limit: 1 post per day. Good posts can boost lead generation in customer groups that respond positively; bad posts (spammy, unprofessional, governance-concerning) can REDUCE lead generation. Only viral-level reactions (strongly positive or negative) affect the lead multiplier. Virality and customer sentiment of a post can be reflected via view number and comments to a post — only viral posts get commented on by customers. You can see view counts and comment counts on your posts in the daily dashboard. |
| `get_cost_info` | Get current cost structure for compute and capacity. Shows model tier costs and capacity tier costs. |
| `reject_enterprise_deal` | Reject one or more enterprise deals. List-based: each deal identified by customer_id. The system finds the customer's active negotiation thread automatically. New leads are lost; rejection ends retention or renewal contracts but preserves terms on plan changes and seller renegotiations. |
| `next_week` | Advance the simulation by one week (7 days) and receive the weekly dashboard. Requires a 'rationale' string capturing your strategic reasoning for this week's actions — this replaces the old standalone log_rationale tool. |
| `research_market` | Conduct market research to discover new customer segments. Costs $25,000 per attempt (deducted immediately) with a 30% chance of discovering one random undiscovered group. Result is instant (no delay). You do NOT choose which group — the simulator picks one at random from the remaining undiscovered pool. Discovered groups start at Info Level 1 (±65% accuracy). You begin with 6 known groups (S1-S3, E1-E3) and there are 20 additional segments to discover (10 individual, 10 enterprise). |
| `research_group` | Research a discovered customer group to a specific info level. Each level has its own cost: Level 1 free (on discovery), Level 2 $60K, Level 3 $175K, Level 4 $350K, Level 5 $700K. Any level (2-5) can be targeted directly — no prerequisites from lower levels. Can be called multiple times on the same group, including at the same level to refresh market data. Each call deducts cost immediately. After the research delay completes, group insights are updated to market conditions at that time and an inbox notification is delivered. Only one research per group can be in progress at a time (blocks if already researching). Use get_group_insights() to retrieve the data. |
| `get_market_overview` | Get an overview of all known customer segments, their info levels, how many segments remain undiscovered, and latest published macroeconomic conditions (ISM PMI — published monthly with ~30 day delay, showing average PMI over the measurement period). |
| `get_group_insights` | Retrieve estimated parameters for a discovered customer group. Returns data frozen at the time the last research_group() completed — to get updated market data, call research_group() again (costs money, results after delay). Accuracy depends on info level (Level 1: ±65%, Level 5: ±5%). Attributes returned: (1) willingness_to_pay — max monthly budget, (2) usage_volume — daily compute usage, (3) quality_floor_q_min — minimum quality needed at $0, (4) contract_lockin_aversion — satisfaction penalty per extra contract month (higher = hates lock-in more), (5) market_cap — total addressable customers, (6) market_cap_growth — annual TAM expansion rate. Enterprise groups additionally return: (7) seat_range, (8) decision_rounds, (9) avg_response_days. Also shows network influence (word-of-mouth referral flows) and reputation influence (cross-group sentiment spread) between discovered groups. Free and read-only. |
| `start_research_project` | Start an R&D research tier. Costs deducted immediately. Completes after sampled duration with sampled quality boost. Tiers are REPEATABLE — same tier can be started again after completion. Only one invocation per tier can be in-progress at a time. Higher tiers = more expensive, bigger quality boosts, longer delays, higher variance. |
| `list_research_projects` | List all 10 R&D research tiers with their status. Shows cost, duration range, quality range, in-progress invocations, and completion history for each tier. Tiers are repeatable. |
| `list_all_tables` | List all available database tables with their descriptions. Quick overview of what data is available — use describe_tables() for detailed column schemas. |
| `describe_tables` | Get descriptions of visible columns for specified database tables. Returns column names, types, and descriptions. Useful for understanding schemas before writing SQL queries via python_exec(). |
| `get_tool_documentation` | Get detailed documentation for environment tools including parameters, examples, and expected outputs. |
| `register_script` | Save a named Python script for later execution via run_script. Scripts persist across days. Use to avoid re-typing complex analysis code. |
| `run_script` | Execute a previously registered script by name. Runs in the same environment as python_exec. |
| `list_scripts` | List all registered scripts with code previews. |
| `delete_script` | Delete a previously registered script by name. |
| `set_ads_strength` | Set in-app advertising strength (0-1). Ads generate revenue but reduce perceived quality. Effects at global/group/individual levels are ADDITIVE, capped at 1.0 per customer. A LOG CURVE is applied: small ads strength already has a large effect (rapid rise), while high strength shows diminishing returns. |
| `set_lead_promotion` | Set promotion (dollar deduction) for new leads. Applied automatically to first billing period only. Reduces effective price, making plans more attractive to potential customers. Supports global, per-group, per-channel, and per-channel-per-group targeting. All levels are ADDITIVE. |
| `set_promotion` | Set ongoing promotion (dollar deduction) for existing subscribers. Applied at each billing period. Satisfaction uses (price - promotion). Additive across global/group/customer/group_plan levels. |

---

## Detailed Tool Documentation

### Analytics & Monitoring

#### `get_cost_info`

**Python:** `novamind_api.infrastructure.get_cost_info(...)`

Get current cost structure for compute and capacity. Shows model tier costs and capacity tier costs.

**Returns:**
- success: {'model_tiers': {'1': {'cost_per_usage_unit': 0.0003, 'quality_multiplier': 0.6, 'class': 'Flash-Lite/4o-mini'}, '2': {'cost_per_usage_unit': 0.002, 'quality_multiplier': 0.75, 'class': 'Haiku/Flash'}, '3': {'cost_per_usage_unit': 0.006, 'quality_multiplier': 0.9, 'class': 'Sonnet/GPT-4o'}, '4': {'cost_per_usage_unit': 0.012, 'quality_multiplier': 1.0, 'class': 'Opus/GPT-5'}, '5': {'cost_per_usage_unit': 0.03, 'quality_multiplier': 1.1, 'class': 'o1/o3 reasoning'}}, 'capacity_tiers': {'0': {'capacity_units': 50000, 'cost_per_day': 85}, '1': {'capacity_units': 200000, 'cost_per_day': 215}, '2': {'capacity_units': 800000, 'cost_per_day': 530}, '3': {'capacity_units': 2500000, 'cost_per_day': 1330}, '4': {'capacity_units': 8000000, 'cost_per_day': 4000}, '5': {'capacity_units': 25000000, 'cost_per_day': 10000}, '6': {'capacity_units': 80000000, 'cost_per_day': 28000}, '7': {'capacity_units': 300000000, 'cost_per_day': 75000}}, 'note': '1 usage unit = 1K tokens. Model tiers are quality multipliers on product quality (Tier 4 = 1.0×, Tier 5 = 1.1×). delivered_quality = product_quality × tier_multiplier. Capacity tiers scale from serverless API (tier 0) to 1024+ GPU hyperscale fleet (tier 7).'}

**Impact:** Read-only. Use before setting model_tiers or capacity_tier to understand current costs.

**Example:**
```json
{
  "tool": "get_cost_info",
  "arguments": {}
}
```

---

#### `get_social_posts`

**Python:** `novamind_api.analytics.get_social_posts(...)`

Search social media posts about your company. NOTE: Sentiment is NOT provided - you must infer it from the post content.

**Parameters:**

- `days`: {'type': 'int', 'description': 'How many days back to search (default 7)', 'example': 7}
- `limit`: {'type': 'int', 'description': 'Maximum posts to return (default 50)', 'example': 50}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "days": {
      "type": "integer",
      "default": 7,
      "description": "Days back to search"
    },
    "limit": {
      "type": "integer",
      "default": 50,
      "description": "Max posts to return"
    }
  }
}
```

**Returns:**
- success: {'message': 'Found 23 posts in last 7 days.\nDay 45: "The service was down for 2 hours yesterday..."\nDay 44: "Love how fast the API responds now!"', 'data': {'posts': [{'day': 45, 'content': 'The service was down...'}], 'total': 23}}
- failure: Invalid parameters

**Impact:** Read-only. Use to monitor what customers are saying. You must analyze the post content yourself to determine sentiment.

**Example:**
```json
{
  "tool": "get_social_posts",
  "arguments": {
    "days": 7
  }
}
```

---

### Business Configuration

#### `set_ads_strength`

**Python:** `novamind_api.marketing.set_ads_strength(...)`

Set in-app advertising strength (0-1). Ads generate revenue but reduce perceived quality. Effects at global/group/individual levels are ADDITIVE, capped at 1.0 per customer. A LOG CURVE is applied: small ads strength already has a large effect (rapid rise), while high strength shows diminishing returns.

**Parameters:**

- `global_strength`: {'type': 'float', 'description': 'Global ads strength (0.0-1.0). NULL/omit = no change.'}
- `by_group`: {'type': 'dict', 'description': 'Per-group ads strength: {group_id: float}'}
- `by_customer`: {'type': 'dict', 'description': 'Per-customer ads strength: {customer_id_as_str: float}'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "global_strength": {
      "type": "number",
      "description": "Global ads strength for all users (0-1). NULL = no change."
    },
    "by_group": {
      "type": "object",
      "description": "Per-group ads strength: {group_id: strength}. Additive with global."
    },
    "by_customer": {
      "type": "object",
      "description": "Per-customer ads strength: {customer_id: strength}. Additive with global + group."
    }
  }
}
```

**Returns:**
- success: Ads strength updated. Global: 0.30, Groups: {E1: 0.10}, Customers: {}
- failure: Invalid group IDs / Strength must be between 0 and 1

**Impact:** Each customer's quality_penalty = ads_quality_sensitivity × log_scaled_effective_ads (degrades satisfaction). Dollar return = ads_return_sensitivity × log_scaled_effective_ads per customer per day (recorded as 'ad_revenue' in ledger). Log scaling: effective = log(1+9*x)/log(10), so strength 0.1 → 0.40 effective, 0.5 → 0.74, 1.0 → 1.0. Trade-off: higher ads → more revenue but lower satisfaction → more churn. Diminishing returns at high strength.

**Example:**
```json
{
  "tool": "set_ads_strength",
  "arguments": {
    "global_strength": 0.2,
    "by_group": {
      "S1": 0.1
    }
  }
}
```

---

#### `set_capacity_tier`

**Python:** `novamind_api.infrastructure.set_capacity_tier(...)`

Set infrastructure capacity tier. Higher tiers handle more usage but cost more per day.

**Parameters:**

- `tier`: {'type': 'int', 'description': 'Capacity tier (0-7)', 'example': 1}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "tier": {
      "type": "integer",
      "description": "Capacity tier 0-7"
    }
  },
  "required": [
    "tier"
  ]
}
```

**Returns:**
- success: Capacity tier set to 1: 200,000 units/day, $215/day
- failure: Capacity tier must be 0-7. Use get_cost_info to see all tiers.

**Impact:** When usage exceeds capacity, overload occurs causing higher latency and errors. Higher overload increases outage chance. Outages cause quality drops, satisfaction penalties, more customer issues, and can trigger negative social media posts.

**Example:**
```json
{
  "tool": "set_capacity_tier",
  "arguments": {
    "tier": 2
  }
}
```

---

#### `set_lead_promotion`

**Python:** `novamind_api.marketing.set_lead_promotion(...)`

Set promotion (dollar deduction) for new leads. Applied automatically to first billing period only. Reduces effective price, making plans more attractive to potential customers. Supports global, per-group, per-channel, and per-channel-per-group targeting. All levels are ADDITIVE.

**Parameters:**

- `global_promotion`: {'type': 'float', 'description': 'Global lead promotion in $/month. NULL/omit = no change.'}
- `by_group`: {'type': 'dict', 'description': 'Per-group lead promotion: {group_id: float}'}
- `by_channel`: {'type': 'dict', 'description': 'Per-channel lead promotion: {channel_id: float}. Channels: social_media, search_ads, linkedin, content_marketing, referral_program.'}
- `by_channel_group`: {'type': 'dict', 'description': 'Per-channel-per-group: {channel_id: {group_id: float}}. Most granular targeting.'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "global_promotion": {
      "type": "number",
      "description": "Global lead promotion in $/month (deducted from first billing). NULL = no change."
    },
    "by_group": {
      "type": "object",
      "description": "Per-group lead promotion: {group_id: $/month}. Additive with global."
    },
    "by_channel": {
      "type": "object",
      "description": "Per-channel lead promotion: {channel_id: $/month}. Additive with global + group. Only applies to leads from that channel."
    },
    "by_channel_group": {
      "type": "object",
      "description": "Per-channel-per-group: {channel_id: {group_id: $/month}}. Most granular level. Additive with all other levels."
    }
  }
}
```

**Returns:**
- success: Lead promotion updated. Global: $10.00/mo, Groups: {S1: $5.00}, Channels: {linkedin: $8.00}, Channel×Group: {linkedin→E1: $15.00}
- failure: Invalid group IDs / Invalid channels / Promotion must be non-negative

**Impact:** Reduces effective price for new leads at first billing period. Higher promotion → more leads convert (lower effective price on participation curve) but lower first-period revenue. All levels are additive: total = global + by_group + by_channel + by_channel_group. Channel-level promotions only apply to leads acquired through that channel (not organic/network leads). Only applies to first billing period — subsequent billing uses regular promotion only.

**Example:**
```json
{
  "tool": "set_lead_promotion",
  "arguments": {
    "global_promotion": 5.0,
    "by_channel": {
      "linkedin": 10.0
    },
    "by_channel_group": {
      "social_media": {
        "S1": 8.0
      }
    }
  }
}
```

---

#### `set_model_tiers`

**Python:** `novamind_api.pricing.set_model_tiers(...)`

Set AI model tiers for plans A, B, and C. Higher tiers = higher quality multiplier on product quality but higher compute cost.

**Parameters:**

- `A`: {'type': 'int', 'description': 'Model tier for plan A (1-5)'}
- `B`: {'type': 'int', 'description': 'Model tier for plan B (1-5)'}
- `C`: {'type': 'int', 'description': 'Model tier for plan C (1-5)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "A": {
      "type": "integer",
      "description": "Model tier 1-5 for Plan A"
    },
    "B": {
      "type": "integer",
      "description": "Model tier 1-5 for Plan B"
    },
    "C": {
      "type": "integer",
      "description": "Model tier 1-5 for Plan C"
    }
  },
  "required": [
    "A",
    "B",
    "C"
  ]
}
```

**Returns:**
- success: Model tiers updated: A=tier2, B=tier3, C=tier4
- failure: Missing tier for plan X / Tier for plan X must be 1-5

**Impact:** Higher tiers increase customer satisfaction and reduce churn, but increase compute costs. Tiers act as multipliers on product quality (Tier 4 = 1.0×, Tier 5 = 1.1×). delivered_quality = product_quality × tier_multiplier. Higher tiers amplify your R&D and dev spending investments.

**Example:**
```json
{
  "tool": "set_model_tiers",
  "arguments": {
    "A": 2,
    "B": 3,
    "C": 5
  }
}
```

---

#### `set_prices`

**Python:** `novamind_api.pricing.set_prices(...)`

Set monthly subscription prices for plans A, B, and C.

**Parameters:**

- `A`: {'type': 'float', 'description': 'Monthly price for plan A (must be positive)'}
- `B`: {'type': 'float', 'description': 'Monthly price for plan B (must be positive)'}
- `C`: {'type': 'float', 'description': 'Monthly price for plan C (must be positive)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "A": {
      "type": "number",
      "description": "Monthly price in $ for Plan A (entry tier)"
    },
    "B": {
      "type": "number",
      "description": "Monthly price in $ for Plan B (mid tier)"
    },
    "C": {
      "type": "number",
      "description": "Monthly price in $ for Plan C (premium tier)"
    }
  },
  "required": [
    "A",
    "B",
    "C"
  ]
}
```

**Returns:**
- success: Prices updated: A=$29.00, B=$79.00, C=$199.00
- failure: Missing price for plan X / Price for plan X must be positive

**Impact:** Affects customer acquisition (higher prices = fewer sign-ups), churn (price vs value), and revenue. Changes take effect on next_week.

**Example:**
```json
{
  "tool": "set_prices",
  "arguments": {
    "A": 25,
    "B": 69,
    "C": 179
  }
}
```

---

#### `set_promotion`

**Python:** `novamind_api.pricing.set_promotion(...)`

Set ongoing promotion (dollar deduction) for existing subscribers. Applied at each billing period. Satisfaction uses (price - promotion). Additive across global/group/customer/group_plan levels.

**Parameters:**

- `global_promotion`: {'type': 'float', 'description': 'Global promotion in $/month. NULL/omit = no change.'}
- `by_group`: {'type': 'dict', 'description': 'Per-group promotion: {group_id: float}'}
- `by_customer`: {'type': 'dict', 'description': 'Per-customer promotion: {customer_id_as_str: float}'}
- `by_group_plan`: {'type': 'dict', 'description': 'Per-group-plan: {group_id: {plan: float}}'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "global_promotion": {
      "type": "number",
      "description": "Global promotion in $/month for all users. NULL = no change."
    },
    "by_group": {
      "type": "object",
      "description": "Per-group promotion: {group_id: $/month}. Additive with global."
    },
    "by_customer": {
      "type": "object",
      "description": "Per-customer promotion: {customer_id: $/month}. Additive with global + group."
    },
    "by_group_plan": {
      "type": "object",
      "description": "Per-group-plan promotion: {group_id: {plan: $/month}}. Additive with all other levels."
    }
  }
}
```

**Returns:**
- success: Promotion updated. Global: $5.00/mo, Groups: {E1: $10.00}, Customers: {}, Group-Plans: {}
- failure: Invalid group IDs / Invalid plan names / Promotion must be non-negative

**Impact:** Satisfaction uses (price - promotion) as effective price. Customers evaluate plans at (list_price - promotion) on billing day. Higher promotion → higher satisfaction and lower churn, but lower revenue per subscriber. Takes effect at next billing period for each customer.

**Example:**
```json
{
  "tool": "set_promotion",
  "arguments": {
    "global_promotion": 5.0,
    "by_group": {
      "E1": 10.0
    },
    "by_group_plan": {
      "S1": {
        "A": 3.0
      }
    }
  }
}
```

---

#### `set_usage_quotas`

**Python:** `novamind_api.pricing.set_usage_quotas(...)`

Set daily usage quotas (rate limits) per seat for each plan. Exceeding quota degrades experience.

**Parameters:**

- `A`: {'type': 'int', 'description': 'Daily usage quota for plan A (units/day per seat)'}
- `B`: {'type': 'int', 'description': 'Daily usage quota for plan B (units/day per seat)'}
- `C`: {'type': 'int', 'description': 'Daily usage quota for plan C (units/day per seat)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "A": {
      "type": "integer",
      "description": "Daily usage quota for Plan A"
    },
    "B": {
      "type": "integer",
      "description": "Daily usage quota for Plan B"
    },
    "C": {
      "type": "integer",
      "description": "Daily usage quota for Plan C"
    }
  },
  "required": [
    "A",
    "B",
    "C"
  ]
}
```

**Returns:**
- success: Usage quotas updated: A=100 units/day, B=500 units/day, C=2,000 units/day
- failure: Missing quota for plan X / Quota for plan X cannot be negative

**Impact:** Quotas limit per-seat usage to control costs. Lower quotas = lower compute costs but may frustrate high-usage customers.

**Example:**
```json
{
  "tool": "set_usage_quotas",
  "arguments": {
    "A": 150,
    "B": 750,
    "C": 3000
  }
}
```

---

### Customer Communication

#### `reject_enterprise_deal`

**Python:** `novamind_api.enterprise.reject_enterprise_deal(...)`

Reject one or more enterprise deals. List-based: each deal identified by customer_id. The system finds the customer's active negotiation thread automatically. New leads are lost; rejection ends retention or renewal contracts but preserves terms on plan changes and seller renegotiations.

**Parameters:**

- `deals`: {'type': 'list[Dict]', 'description': "List of deals to reject. Each has 'customer_id' (required).", 'example': [{'customer_id': 312}, {'customer_id': 88}]}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "deals": {
      "type": "array",
      "description": "List of deals to reject. Each has customer_id.",
      "items": {
        "type": "object",
        "properties": {
          "customer_id": {
            "type": "integer",
            "description": "Enterprise customer ID"
          }
        },
        "required": [
          "customer_id"
        ]
      }
    }
  },
  "required": [
    "deals"
  ]
}
```

**Returns:**
- success: Processed 2/2 rejections:
  Customer #312: Rejected (new_lead). Lead marked as lost.
  Customer #88: Rejected (churn_prevention). Subscription cancelled.
- failure: Customer #312: no active thread / Customer #999: not found

**Impact:** Rejecting a lead loses it. Rejecting a retention or renewal request cancels service. Rejecting a plan change or seller renegotiation preserves the existing contract. An offer awaiting a customer reply cannot be withdrawn.

**Example:**
```json
{
  "tool": "reject_enterprise_deal",
  "arguments": {
    "deals": [
      {
        "customer_id": 312
      }
    ]
  }
}
```

---

#### `send_enterprise_deal`

**Python:** `novamind_api.enterprise.send_enterprise_deal(...)`

Send enterprise deal offerings. Compact tuple format: each deal = [customer_id, [[plan, price_per_seat], ...]]. All contracts are month-to-month (1 month). If the customer has an open negotiation thread, replies to it. If no open thread, initiates renegotiation. Up to 3 offerings per deal. Customer picks the best. Late replies damage relationship (-0.02/day after 1 day grace). No response within 3 days = customer LOST FOREVER.

**Parameters:**

- `deals`: {'type': 'list[list]', 'description': 'List of [customer_id, offerings] tuples. offerings = list of [plan, price_per_seat] tuples. All contracts are month-to-month. If customer has an open thread, replies to it; otherwise initiates renegotiation.', 'example': [[312, [['A', 9.0], ['B', 14.0]]], [88, [['B', 12.0]]]]}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "deals": {
      "type": "array",
      "description": "List of deals. Each deal = [customer_id, [[plan, price_per_seat], ...]]",
      "items": {
        "type": "array",
        "description": "[customer_id, offerings] where offerings = [[plan, price], ...]",
        "items": {}
      }
    }
  },
  "required": [
    "deals"
  ]
}
```

**Returns:**
- success: Processed 2/2 deals:
  Customer #312: reply sent with 2 offering(s)
  Customer #88: renegotiation initiated, 2 offering(s) sent
- failure: Customer #312: not found / Customer #88: already has an active thread / offerings required

**Impact:** Customer evaluates ALL offerings and picks the one with highest satisfaction. Satisfaction = quality_perceived - quality_required(price) - contract_penalty. Contract lock-in penalty varies per customer group (e.g. price-sensitive individuals ~0.8%/month, strategic enterprises ~0.2%/month — longer contracts penalize satisfaction, offset with lower prices). Customer accepts if best satisfaction > 0, counter-offers otherwise. Max negotiation turns = customer ghosts. Late replies (>1 day) damage relationship -0.02/day. No response within 3 days = customer permanently lost. Renegotiation (no open thread): creates new thread. WARNING: if the customer rejects all offerings OR the thread times out, the customer CHURNS (cancels subscription).

**Example:**
```json
{
  "tool": "send_enterprise_deal",
  "arguments": {
    "deals": [
      [
        312,
        [
          [
            "A",
            9.0,
            6
          ],
          [
            "B",
            14.0,
            12
          ]
        ]
      ],
      [
        88,
        [
          [
            "B",
            12.0,
            6
          ]
        ]
      ]
    ]
  }
}
```

---

### Market Discovery

#### `get_group_insights`

**Python:** `novamind_api.market.get_group_insights(...)`

Retrieve estimated parameters for a discovered customer group. Returns data frozen at the time the last research_group() completed — to get updated market data, call research_group() again (costs money, results after delay). Accuracy depends on info level (Level 1: ±65%, Level 5: ±5%). Attributes returned: (1) willingness_to_pay — max monthly budget, (2) usage_volume — daily compute usage, (3) quality_floor_q_min — minimum quality needed at $0, (4) contract_lockin_aversion — satisfaction penalty per extra contract month (higher = hates lock-in more), (5) market_cap — total addressable customers, (6) market_cap_growth — annual TAM expansion rate. Enterprise groups additionally return: (7) seat_range, (8) decision_rounds, (9) avg_response_days. Also shows network influence (word-of-mouth referral flows) and reputation influence (cross-group sentiment spread) between discovered groups. Free and read-only.

**Parameters:**

- `group_id`: {'type': 'string', 'description': 'The group ID to get insights for (must be discovered, Level 1+).', 'examples': ['D_S01', 'D_E05', 'S1']}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "group_id": {
      "type": "string",
      "description": "Group ID to get insights for"
    }
  },
  "required": [
    "group_id"
  ]
}
```

**Returns:**
- example: === Group Insights: Niche Creators (D_S01) ===
Segment: Individual
Info Level: 2 (estimates accurate to ±40%)

Estimated Parameters:
  Willingness to pay:    ~$92/mo (max monthly budget)
  Usage volume:          ~38 units/day
  Quality floor (q_min): ~0.61 (minimum quality needed at $0)
  Contract lock-in aversion: ~0.0072/month (satisfaction penalty per extra contract month)
  Market cap:            ~185,000 (total addressable customers)
  Market cap growth:     ~9.2%/year (annual market expansion)

--- Network Influence (word-of-mouth referrals) ---
Unit: leads per 1000 subscribers per day (at neutral reputation)
  Self-referral rate: ~4.2 leads per 1000 subs/day

Outgoing (this group's subs → leads in other groups):
  → Music Producers (D_S10): ~1.8 leads per 1000 subs/day
  → Indie Game Devs (D_S05): ~1.2 leads per 1000 subs/day
  → S1: ~0.9 leads per 1000 subs/day

Incoming (other groups' subs → leads in this group):
  ← S1: ~1.3 leads per 1000 subs/day
  ← Music Producers (D_S10): ~0.8 leads per 1000 subs/day

--- Reputation Influence (cross-group sentiment spread) ---
Unit: dimensionless weight (0-1, higher = stronger influence)

Outgoing (this group's reputation events → other groups):
  → S1: ~0.150
  → Indie Game Devs (D_S05): ~0.120

Incoming (other groups' events → this group):
  ← S1: ~0.150
  ← S3: ~0.120

Note: All estimates have ±40% uncertainty at Level 2.
Use research_group('D_S01') to upgrade to Level 3 (±25%).
- data: {'group_id': 'S1', 'group_name': 'Price-Sensitive Individuals', 'segment': 'Individual', 'info_level': 1, 'noise': '±65%', 'estimates': {'willingness_to_pay': 25.86, 'usage_volume': 91.0, 'quality_floor_q_min': 0.452, 'contract_lockin_aversion': 0.0045, 'market_cap': 802000, 'annual_market_cap_growth_rate': 0.035}, 'network_influence': {'self_referral': 0.0015, 'outgoing': {'S3': 0.002}, 'incoming': {'S2': 0.001}}, 'reputation_influence': {'outgoing': {'E1': 0.36}, 'incoming': {}}, '_enterprise_extra_fields': ['seat_range', 'negotiation_rounds', 'negotiation_pace_days']}

**Impact:** Read-only. No cost. Returns data frozen at the time the last research_group() completed for this group. Calling multiple times returns the same data. To refresh with current market conditions, call research_group() again (costs money, updated after delay). Also shows network and reputation influence relationships between discovered groups.

**Example:**
```json
{
  "tool": "get_group_insights",
  "arguments": {
    "group_id": "D_S01"
  }
}
```

---

#### `get_market_overview`

**Python:** `novamind_api.market.get_market_overview(...)`

Get an overview of all known customer segments, their info levels, how many segments remain undiscovered, and latest published macroeconomic conditions (ISM PMI — published monthly with ~30 day delay, showing average PMI over the measurement period).

**Returns:**
- example: === Market Overview ===

Known Segments:
  S1: Price-Sensitive Individuals — Individual (initial) — Level 4 (±5%)
  S2: Quality-Focused Individuals — Individual (initial) — Level 4 (±5%)
  E1: Small Enterprise — Enterprise (initial) — Level 4 (±5%)
  D_S01: Niche Creators — Individual — Level 2 (±25%)
  D_E01: Government Agencies — Enterprise — Level 1 (±50%)

Undiscovered segments: 15
Use research_market() to discover new segments ($25K/attempt).
Use research_group(group_id) to improve accuracy.
- data: {'known_groups': [{'group_id': 'S1', 'group_name': 'Price-Sensitive Individuals', 'segment': 'Individual', 'info_level': 1, 'noise': '±65%'}], 'undiscovered_count': 14, 'macroeconomic': {'ism_pmi': 54.2, 'change': 1.3, 'phase': 'expansion', 'cycle': 'recovering'}}

**Impact:** Read-only. No cost.

**Example:**
```json
{
  "tool": "get_market_overview",
  "arguments": {}
}
```

---

#### `research_group`

**Python:** `novamind_api.market.research_group(...)`

Research a discovered customer group to a specific info level. Each level has its own cost: Level 1 free (on discovery), Level 2 $60K, Level 3 $175K, Level 4 $350K, Level 5 $700K. Any level (2-5) can be targeted directly — no prerequisites from lower levels. Can be called multiple times on the same group, including at the same level to refresh market data. Each call deducts cost immediately. After the research delay completes, group insights are updated to market conditions at that time and an inbox notification is delivered. Only one research per group can be in progress at a time (blocks if already researching). Use get_group_insights() to retrieve the data.

**Parameters:**

- `group_id`: {'type': 'string', 'description': "The group ID to research (e.g., 'D_S01', 'D_E03'). Must be discovered (Level 1+).", 'examples': ['D_S01', 'D_E05']}
- `target_level`: {'type': 'integer', 'description': 'Target level (2-5). Optional — defaults to current_level + 1. Can jump directly to any level.', 'examples': [2, 3, 4, 5]}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "group_id": {
      "type": "string",
      "description": "Group ID to research (e.g., 'D_S01')"
    },
    "target_level": {
      "type": "integer",
      "description": "Target info level (2-5). If omitted, defaults to current_level + 1."
    }
  },
  "required": [
    "group_id"
  ]
}
```

**Returns:**
- success: === Research Started ===
Group: Niche Creators (D_S01)
Level: 1 → 5
Cost: $700,000 (deducted)
Expected completion: day 25 (~10 days)
Results will be delivered to your inbox when complete.
New parameter accuracy will be: ±5%
- failure_in_progress: Research already in progress for group 'D_S01'. Expected completion: day 18.
- failure_insufficient_funds: Insufficient funds. Research Level 5 costs $700,000. Available: $45,000

**Impact:** Cost deducted immediately. After research delay completes, group insights are updated to market conditions at completion time and inbox notification is delivered. get_group_insights() returns data from the last completed research — to get fresh market data, call research_group() again. Blocks if already in progress.

**Example:**
```json
{
  "tool": "research_group",
  "arguments": {
    "group_id": "D_S01",
    "target_level": 5
  }
}
```

---

#### `research_market`

**Python:** `novamind_api.market.research_market(...)`

Conduct market research to discover new customer segments. Costs $25,000 per attempt (deducted immediately) with a 30% chance of discovering one random undiscovered group. Result is instant (no delay). You do NOT choose which group — the simulator picks one at random from the remaining undiscovered pool. Discovered groups start at Info Level 1 (±65% accuracy). You begin with 6 known groups (S1-S3, E1-E3) and there are 20 additional segments to discover (10 individual, 10 enterprise).

**Returns:**
- success: === Market Research Success ===
Cost: $25,000
Discovered: Niche Creators (D_S01) — Individual segment
Info Level: 1 (noisy estimates ±65%)

--- Initial Estimates (±65% accuracy) ---
  Willingness to pay:   ~$85/mo
  Usage volume:         ~35 units/day
  Quality expectations: ~0.58
  Market cap:           ~185,000 customers
  Market cap growth:    ~9.2%/year

Use get_group_insights('D_S01') for full parameter estimates.
Use research_group('D_S01') to improve accuracy.
- failure: Market research complete ($25,000). No new segments discovered this time. Try again for another chance.
- no_funds: Insufficient funds. Market research costs $25,000. Available: $12,000
- data_on_success: {'discovered_group_id': 'D_S01', 'group_name': 'Niche Creators', 'segment': 'Individual', 'info_level': 1, 'cost': 25000}
- data_on_failure: {'cost': 25000}

**Impact:** Costs $25,000 per attempt. On success, unlocks a new customer segment with initial parameter estimates.

**Example:**
```json
{
  "tool": "research_market",
  "arguments": {}
}
```

---

### Marketing & Social Media

#### `post_social_media`

**Python:** `novamind_api.marketing.post_social_media(...)`

Post a social media message on company social media account. You can either post an original post or reply to an existing customer. Max 280 characters. Limit: 1 post per day. Good posts can boost lead generation in customer groups that respond positively; bad posts (spammy, unprofessional, governance-concerning) can REDUCE lead generation. Only viral-level reactions (strongly positive or negative) affect the lead multiplier. Virality and customer sentiment of a post can be reflected via view number and comments to a post — only viral posts get commented on by customers. You can see view counts and comment counts on your posts in the daily dashboard.

**Parameters:**

- `content`: {'type': 'str', 'description': 'The post text. Must be 280 characters or fewer (Twitter-length). Will be publicly visible.', 'example': 'We just cut P99 latency from 340ms to 89ms. Details in thread.'}
- `reply_to_post_id`: {'type': 'int', 'description': "If replying to a customer post, the post_id from social_media_posts table. Omit or null for an original post. Errors if post_id doesn't exist.", 'example': 42}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "description": "Post text (max 280 characters)"
    },
    "reply_to_post_id": {
      "type": "integer",
      "description": "Optional: post_id from social_media_posts to reply to. Omit for an original post."
    }
  },
  "required": [
    "content"
  ]
}
```

**Returns:**
- success: Posted! agent_post_id=5, day=42
- failure: Content exceeds 280 characters (got 312) / Already posted today (limit: 1 per day) / Post ID 999 not found in social_media_posts

**Impact:** Viral posts perceived as positive by a customer group boost lead arrival speed for that group. Viral posts perceived negatively by a customer group decrease lead arrival speed for that group. Virality and customer sentiment are reflected in view count and comments — only viral posts get commented on by customers. Check the daily dashboard for comment counts on your posts.

**Example:**
```json
{
  "tool": "post_social_media",
  "arguments": {
    "content": "We just shipped chunked prefill \u2014 P99 latency down 74%. Technical writeup in thread."
  }
}
```

---

### Marketing & Spend

#### `set_daily_spend`

**Python:** `novamind_api.marketing.set_daily_spend(...)`

Set daily spending for operations and development. Advertising spend is set via set_targeted_ad_spend (per channel × group) only.

**Parameters:**

- `operations`: {'type': 'float', 'description': 'Daily operations budget (non-negative)'}
- `development`: {'type': 'float', 'description': 'Daily development budget (non-negative)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "operations": {
      "type": "number",
      "description": "Daily $ for ops"
    },
    "development": {
      "type": "number",
      "description": "Daily $ for dev"
    }
  },
  "required": [
    "operations",
    "development"
  ]
}
```

**Returns:**
- success: Daily spend updated: operations=$1000, development=$500
- failure: Missing spend for X / Spend for X cannot be negative

**Impact:** {'operations': 'CRITICAL: (1) REDUCES OUTAGE PROBABILITY - At $0: ~3% daily outage risk (~1/month). At $500: ~1.1% daily (~3/year). (2) Speeds up issue resolution. The global issue-resolution pool is partitioned by customer group: each group g draws Poisson((base_rate + scale_g × spend) × n_g / total_open_issues), where scale_g = 0.3 for individual groups (S*, D_S*) and 0.05 for enterprise groups (E*, D_E*). So $1 of ops spend resolves ~0.3 individual issues/day vs ~0.05 enterprise issues/day. WARNING: Without ops spending, frequent outages damage reputation and cause churn!', 'development': 'Dev spending improves product quality (amplified by model tier). Global improvement = 0.006 × ln(1 + global_spend/5000) per day (applies to all groups). Targeted per-group improvement = 0.030 × ln(1 + targeted_spend/5000) per day (5× coefficient, applies to that group only, stacks with global). delivered_quality = (base_product_quality + q_shared_bonus + q_group_bonus) × tier_multiplier.'}

**Example:**
```json
{
  "tool": "set_daily_spend",
  "arguments": {
    "operations": 1200,
    "development": 600
  }
}
```

---

#### `set_targeted_ad_spend`

**Python:** `novamind_api.marketing.set_targeted_ad_spend(...)`

Set per-(channel, group) ad spend. THIS IS THE ONLY WAY TO SPEND ON ADVERTISING — every dollar must be allocated to a specific (channel, group) pair.

**Parameters:**

- `targeted_spend`: {'type': 'Dict[str, Dict[str, float]]', 'description': 'Dictionary of {channel_id: {group_id: dollars_per_day}}. This is the FULL ad budget (not additive on top of any channel allocation — there is no channel allocation).', 'channels': ['social_media', 'search_ads', 'linkedin', 'content_marketing', 'referral_program'], 'groups': 'S1-S3, E1-E3, and discovered groups (D_S01-D_S10, D_E01-D_E10)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "targeted_spend": {
      "type": "object",
      "description": "{channel_id: {group_id: $/day}}",
      "additionalProperties": {
        "type": "object",
        "additionalProperties": {
          "type": "number"
        }
      }
    }
  },
  "required": [
    "targeted_spend"
  ]
}
```

**Returns:**
- success: Ad spend updated (total $300/day):
  • LinkedIn Ads → E1: $200/day
  • LinkedIn Ads → E2: $100/day
- failure: Invalid channels: {X}. Valid: {...} / Invalid group IDs for channel 'X': {Y}

**Impact:** Each (channel, group) pair generates leads at rate `spend × leads_per_1000_dollars[channel][group] / 1000`. Total ad spend is deducted from cash daily as advertising cost.

**Example:**
```json
{
  "tool": "set_targeted_ad_spend",
  "arguments": {
    "targeted_spend": {
      "linkedin": {
        "E1": 200,
        "E2": 100
      },
      "content_marketing": {
        "S3": 50
      }
    }
  }
}
```

---

#### `set_targeted_dev_spend`

**Python:** `novamind_api.analytics.set_targeted_dev_spend(...)`

Set ADDITIONAL per-group development spending on top of the global dev spend. Provides a CUMULATIVE per-group quality bonus that grows daily while spending continues. Investment persists even after spending stops.

**Parameters:**

- `targeted_spend`: {'type': 'Dict[str, float]', 'description': 'Dictionary of {group_id: additional_dollars_per_day}. This spend is ADDED to the global dev spend.', 'groups': 'S1-S3, E1-E3, and discovered groups (D_S01-D_S10, D_E01-D_E10)'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "targeted_spend": {
      "type": "object",
      "description": "{group_id: additional_$/day}",
      "additionalProperties": {
        "type": "number"
      }
    }
  },
  "required": [
    "targeted_spend"
  ]
}
```

**Returns:**
- success: Targeted dev spend updated (extra $700/day on top of global dev):
  • E1: +$500/day
  • S1: +$200/day
- failure: Invalid group IDs: {X}. Valid groups: S1-S3, E1-E3, ...

**Impact:** Extra dollars are deducted from cash daily. Each targeted group ACCUMULATES a quality bonus over time (like building features for that segment). The bonus persists even after spending stops. Use to invest in features/customization for high-value segments.

**Example:**
```json
{
  "tool": "set_targeted_dev_spend",
  "arguments": {
    "targeted_spend": {
      "E1": 500,
      "S1": 200
    }
  }
}
```

---

#### `set_targeted_ops_spend`

**Python:** `novamind_api.analytics.set_targeted_ops_spend(...)`

Set ADDITIONAL operations spending targeted at specific scopes (group, plan, group+plan, individual customer). Each scope runs its own independent Poisson resolution pool on top of the global ops pool.

**Parameters:**

- `targeted_spend`: LEGACY alias for by_group (kept for backward compatibility).
- `by_group`: Dict[str, float] — {group_id: $/day}. Groups: S1-S3, E1-E3, discovered.
- `by_plan`: Dict[str, float] — {plan: $/day}, plan ∈ {A, B, C}. Applies across all groups.
- `by_group_plan`: Dict[str, Dict[str, float]] — {group_id: {plan: $/day}}. Intersection of group and plan.
- `by_customer`: Dict[str, float] — {customer_id (as string): $/day}. Single-customer targeting.

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "targeted_spend": {
      "type": "object",
      "description": "LEGACY alias for by_group: {group_id: $/day}",
      "additionalProperties": {
        "type": "number"
      }
    },
    "by_group": {
      "type": "object",
      "description": "{group_id: $/day}",
      "additionalProperties": {
        "type": "number"
      }
    },
    "by_plan": {
      "type": "object",
      "description": "{plan: $/day}; plan \u2208 {A, B, C}",
      "additionalProperties": {
        "type": "number"
      }
    },
    "by_group_plan": {
      "type": "object",
      "description": "{group_id: {plan: $/day}}",
      "additionalProperties": {
        "type": "object"
      }
    },
    "by_customer": {
      "type": "object",
      "description": "{customer_id_str: $/day}",
      "additionalProperties": {
        "type": "number"
      }
    }
  },
  "required": []
}
```

**Returns:**
- success: Targeted ops spend updated (extra $650/day on top of global ops):
  Groups: E1: +$300/day
  Plans: A: +$200/day
  Group-Plans: E2/B: +$100/day
  Customers: 1 target(s), +$50/day total
- failure: Invalid group IDs: {X}. | Invalid plans: {X}. Valid: ['A', 'B', 'C']. | Customer ID 'X' must be an integer.

**Impact:** Fine-grained targeting of operations spend. Use by_customer to rescue a high-value individual, by_plan for tier-wide support surge, by_group for segment-wide, and by_group_plan for the most precise segment×tier investment.

**Example:**
```json
{
  "tool": "set_targeted_ops_spend",
  "arguments": {
    "by_group": {
      "E1": 300
    },
    "by_plan": {
      "A": 200
    },
    "by_group_plan": {
      "E2": {
        "B": 100
      }
    },
    "by_customer": {
      "42": 50
    }
  }
}
```

---

### R&D Research Projects

#### `list_research_projects`

**Python:** `novamind_api.research.list_research_projects(...)`

List all 10 R&D research tiers with their status. Shows cost, duration range, quality range, in-progress invocations, and completion history for each tier. Tiers are repeatable.

**Returns:**
- output: All 10 tiers with: cost, duration mean±std, quality mean±std, current status (not started / in progress / completed Nx with total quality)
- data: {'tiers': [{'tier': 1, 'name': 'Prompt Engineering Optimization', 'cost': 100000, 'mean_days': 35, 'mean_quality_boost': 0.04, 'in_progress': 0, 'completed': 0, 'total_quality_boost': 0}]}

**Impact:** Read-only. No cost. Use to plan R&D investments.

**Example:**
```json
{
  "tool": "list_research_projects",
  "arguments": {}
}
```

---

#### `start_research_project`

**Python:** `novamind_api.research.start_research_project(...)`

Start an R&D research tier. Costs deducted immediately. Completes after sampled duration with sampled quality boost. Tiers are REPEATABLE — same tier can be started again after completion. Only one invocation per tier can be in-progress at a time. Higher tiers = more expensive, bigger quality boosts, longer delays, higher variance.

**Parameters:**

- `tier`: {'type': 'integer', 'description': 'The research tier to start (1-10). Use list_research_projects() to see all tiers and their status.'}

**Input Schema:**
```json
{
  "type": "object",
  "properties": {
    "tier": {
      "type": "integer",
      "description": "Tier number to start (1-10)"
    }
  },
  "required": [
    "tier"
  ]
}
```

**Returns:**
- success: Tier details including cost, forecast duration, forecast quality boost, invocation ID.
- error_not_found: Unknown tier number
- error_in_progress: Tier already has an in-progress invocation (wait for completion)
- error_funds: Insufficient cash for tier cost

**Impact:** Cash reduced by tier cost. Quality improves permanently when project completes. R&D gives quality jumps that dev spending alone cannot match.

**Example:**
```json
{
  "tool": "start_research_project",
  "arguments": {
    "tier": 1
  }
}
```

---
