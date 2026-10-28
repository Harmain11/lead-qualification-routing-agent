import json,os
from pathlib import Path
import psycopg
W='9edba2cc-c627-464c-9ddd-77bfeddb78f9'
WS='workspace_routing_configs workspace_commercial_settings workspace_email_automation_settings workspace_icp_versions workspace_public_intake workspace_notification_configs workspace_operations_policies workspace_provider_lead_approval_policies workspace_email_templates workspace_automations'.split()
WCORE='companies contacts source_events leads lead_suppressions'.split()
LCORE='lead_contacts lead_follow_up_emails lead_collaboration_notes lead_live_presence lead_deals'.split()
LEAD='analysis_runs qualification_results routing_decisions lead_outcomes pipeline_events'.split()
def get(cur,t,where,args):
 cur.execute(f'select row_to_json(x) from (select * from {t} where {where}) x',args)
 return [r[0] for r in cur.fetchall()]
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 cur=c.cursor(); d={'workspaces':get(cur,'workspaces','id=%s',(W,)),'users':get(cur,'users','true',())}
 for t in WS+WCORE: d[t]=get(cur,t,'workspace_id=%s',(W,))
 for t in LCORE+LEAD: d[t]=get(cur,t,'lead_id in (select id from leads where workspace_id=%s)',(W,))
 for u in d['users']:
  if 'password_hash' in u: u['password_hash']='preview-import-disabled'
 Path('replit-preview-export.json').write_text(json.dumps({'source_workspace_id':W,'tables':d},default=str),encoding='utf-8')
 print(json.dumps({k:len(v) for k,v in d.items()}))
