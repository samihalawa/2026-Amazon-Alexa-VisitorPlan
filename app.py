#!/usr/bin/env python3
"""VisitorPlan: a simulated Alexa+ experience backed by a real MCP planning service."""
import base64,hashlib,html,io,itertools,json,os,re,secrets,sqlite3,threading,time,urllib.parse,urllib.request,urllib.error,uuid
from datetime import date,datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo
from jsonschema import validate,ValidationError

ROOT=Path(__file__).parent
DB=os.environ.get('VISITORPLAN_DB',str(ROOT/'visitorplan.sqlite3'))
HOST=os.environ.get('HOST','127.0.0.1');PORT=int(os.environ.get('PORT','8877'))
PROTOCOL='2025-11-25';LOCK=threading.RLock();MCP_SESSIONS={}
ORIGINS={'http://127.0.0.1:'+str(PORT),'http://localhost:'+str(PORT)}|set(filter(None,os.environ.get('APP_ORIGINS','').split(',')))
CATALOG=[
 {'id':'riverlight','name':'Riverlight Museum','category':'art','description':'A fictional hands-on art museum beside the river.','price_cents':1400,'duration_minutes':90,'capacity':8,'accessible':True,'area':'Riverside','tags':['art','history','indoor','family']},
 {'id':'cedar','name':'Cedar Glasshouse','category':'nature','description':'A fictional covered botanical garden with a quiet discovery trail.','price_cents':1200,'duration_minutes':75,'capacity':12,'accessible':True,'area':'Riverside','tags':['nature','indoor','quiet','family']},
 {'id':'orbit','name':'Orbit Science Lab','category':'science','description':'A fictional interactive science space for curious visitors.','price_cents':1800,'duration_minutes':90,'capacity':6,'accessible':True,'area':'North Quarter','tags':['science','indoor','family']},
 {'id':'hilltrail','name':'Hilltop Discovery Trail','category':'nature','description':'A fictional outdoor trail with stairs and panoramic viewpoints.','price_cents':0,'duration_minutes':90,'capacity':10,'accessible':False,'area':'Hilltop','tags':['nature','outdoor','active']},
]
SLOTS=['09:00','11:30','14:00'];VENUES={x['id']:x for x in CATALOG}
def utcnow():return datetime.now(timezone.utc).isoformat()
def connection():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
def initialize_db():
 with connection() as c:
  c.executescript('''
  CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,visitor TEXT,data TEXT,created REAL);
  CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,visitor TEXT,plan TEXT UNIQUE,data TEXT,status TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS allocations(reservation TEXT,venue TEXT,day TEXT,slot TEXT,seats INTEGER,PRIMARY KEY(reservation,venue,day,slot));
  CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,visitor TEXT,kind TEXT,data TEXT,created TEXT);
  ''')
def log_operation(c,visitor,kind,data):
 c.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(str(uuid.uuid4()),visitor,kind,json.dumps(data),utcnow()))
def valid_day(value):
 try:d=date.fromisoformat(value)
 except (ValueError,TypeError):raise ValueError('Choose a valid visit date.')
 if d<datetime.now(ZoneInfo('Europe/Madrid')).date():raise ValueError('Choose today or a future date.')
 if d>datetime.now(ZoneInfo('Europe/Madrid')).date()+timedelta(days=90):raise ValueError('Choose a date within the next 90 days.')
 return d.isoformat()
def valid_visitor(value):
 try:uuid.UUID(value)
 except (ValueError,TypeError):raise ValueError('A valid visitor session is required.')
 return value
def available(c,venue,day,slot):
 used=c.execute('SELECT coalesce(sum(allocations.seats),0) FROM allocations JOIN reservations ON reservations.id=allocations.reservation WHERE allocations.venue=? AND allocations.day=? AND allocations.slot=? AND reservations.status=?',(venue,day,slot,'CONFIRMED_DEMO')).fetchone()[0]
 return VENUES[venue]['capacity']-used
def minutes(value):return int(value[:2])*60+int(value[3:])
def hour(value):return f'{value//60:02d}:{value%60:02d}'
def list_attractions(day:str,people:int=2,accessible:bool=False)->dict:
 day=valid_day(day)
 with LOCK,connection() as c:
  venues=[dict(v,slots=[{'time':slot,'remaining':available(c,v['id'],day,slot),'fits_group':available(c,v['id'],day,slot)>=people} for slot in SLOTS]) for v in CATALOG if not accessible or v['accessible']]
 return {'date':day,'people':people,'inventory':'fictional-demo','attractions':venues}
def plan_visit(visitor_id:str,day:str,people:int,budget_eur:float,interests:list,start_time:str='11:30',accessible:bool=False)->dict:
 valid_visitor(visitor_id);day=valid_day(day)
 if not re.fullmatch(r'(?:0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]',start_time):raise ValueError('Choose a valid start time.')
 budget_cents=round(budget_eur*100);interest_set=set(interests);choices=[]
 with LOCK,connection() as c:
  eligible=[v for v in CATALOG if not accessible or v['accessible']]
  for count in [1,2]:
   for venues in itertools.permutations(eligible,count):
    total=sum(v['price_cents']*people for v in venues)
    if total>budget_cents:continue
    itinerary=[];earliest=minutes(start_time);travel_total=0
    for v in venues:
     travel=0 if not itinerary else (15 if itinerary[-1]['area']==v['area'] else 30)
     earliest+=travel;travel_total+=travel
     slot=next((s for s in SLOTS if minutes(s)>=earliest and available(c,v['id'],day,s)>=people),None)
     if not slot:break
     end=minutes(slot)+v['duration_minutes']
     itinerary.append({'venue_id':v['id'],'name':v['name'],'area':v['area'],'start':slot,'end':hour(end),'price_cents':v['price_cents']*people,'accessible':v['accessible'],'travel_minutes_before':travel,'available_seats':available(c,v['id'],day,slot)})
     earliest=end
    if len(itinerary)!=count:continue
    score=sum(len(interest_set.intersection(v['tags']))*12 for v in venues)+count*5-travel_total/20-total/100000
    choices.append((score,itinerary,total))
  if not choices:return {'available':False,'message':'No itinerary fits this group, date and budget. Try fewer people, an earlier time or another date.','inventory':'fictional-demo'}
  _,itinerary,total=max(choices,key=lambda x:x[0]);pid=str(uuid.uuid4())
  plan={'id':pid,'date':day,'people':people,'activities':itinerary,'total_cents':total,'budget_cents':budget_cents,'remaining_cents':budget_cents-total,'accessible_requested':accessible,'interests':interests,'status':'PROPOSED_DEMO','inventory':'fictional-demo','expires_at':(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),'reason':'Checks current remaining places, allows travel time and stays within your total group budget.'}
  c.execute('INSERT INTO plans VALUES(?,?,?,?)',(pid,visitor_id,json.dumps(plan),time.time()));log_operation(c,visitor_id,'plan_created',{'plan':pid})
  return dict(plan,available=True)
def confirm_plan(visitor_id:str,plan_id:str,confirmed:bool)->dict:
 valid_visitor(visitor_id)
 if confirmed is not True:raise ValueError('Confirm the itinerary before making a demo reservation.')
 with LOCK,connection() as c:
  c.execute('BEGIN IMMEDIATE')
  existing=c.execute('SELECT * FROM reservations WHERE plan=? AND visitor=?',(plan_id,visitor_id)).fetchone()
  if existing:
   if existing['status']=='CANCELLED_DEMO':raise ValueError('This demo reservation was cancelled. Create a fresh plan.')
   return dict(json.loads(existing['data']),idempotent=True)
  row=c.execute('SELECT * FROM plans WHERE id=? AND visitor=?',(plan_id,visitor_id)).fetchone()
  if not row:raise ValueError('This itinerary is not in your visitor session.')
  if time.time()-row['created']>600:raise ValueError('This itinerary has expired. Ask for a fresh availability check.')
  plan=json.loads(row['data'])
  for activity in plan['activities']:
   if available(c,activity['venue_id'],plan['date'],activity['start'])<plan['people']:raise ValueError('Availability changed before confirmation. Ask for a fresh plan.')
  rid=str(uuid.uuid4());reservation={'id':rid,'reference':'VP-'+secrets.token_hex(4).upper(),'plan_id':plan_id,'date':plan['date'],'people':plan['people'],'activities':plan['activities'],'total_cents':plan['total_cents'],'status':'CONFIRMED_DEMO','inventory':'fictional-demo','created':utcnow(),'message':'Your demo reservation is recorded. No real attraction has been booked and no payment has been taken.'}
  c.execute('INSERT INTO reservations VALUES(?,?,?,?,?,?)',(rid,visitor_id,plan_id,json.dumps(reservation),reservation['status'],reservation['created']))
  for a in plan['activities']:c.execute('INSERT INTO allocations VALUES(?,?,?,?,?)',(rid,a['venue_id'],plan['date'],a['start'],plan['people']))
  log_operation(c,visitor_id,'reservation_confirmed',{'reservation':rid});return reservation
def list_reservations(visitor_id:str)->dict:
 valid_visitor(visitor_id)
 with connection() as c:return {'reservations':[dict(json.loads(r['data']),status=r['status']) for r in c.execute('SELECT * FROM reservations WHERE visitor=? ORDER BY created DESC',(visitor_id,))]}
def cancel_reservation(visitor_id:str,reservation_id:str,confirmed:bool)->dict:
 valid_visitor(visitor_id)
 if confirmed is not True:raise ValueError('Confirm cancellation before releasing the demo places.')
 with LOCK,connection() as c:
  row=c.execute('SELECT * FROM reservations WHERE id=? AND visitor=?',(reservation_id,visitor_id)).fetchone()
  if not row:raise ValueError('This reservation is not in your visitor session.')
  duplicate=row['status']=='CANCELLED_DEMO';c.execute('UPDATE reservations SET status=? WHERE id=?',('CANCELLED_DEMO',reservation_id));log_operation(c,visitor_id,'reservation_cancelled',{'reservation':reservation_id})
  return {'id':reservation_id,'reference':json.loads(row['data'])['reference'],'status':'CANCELLED_DEMO','places_released':True,'idempotent':duplicate,'message':'The demo reservation is cancelled and its places are available again.'}

def schema(properties,required):return {'type':'object','properties':properties,'required':required,'additionalProperties':False}
STR={'type':'string'};VISITOR={'type':'string','format':'uuid'};PEOPLE={'type':'integer','minimum':1,'maximum':12}
TOOLS=[
 {'name':'list_attractions','description':'Check fictional-demo attractions and current remaining places by date.','inputSchema':schema({'day':STR,'people':PEOPLE,'accessible':{'type':'boolean'}},['day']),'annotations':{'readOnlyHint':True,'openWorldHint':False}},
 {'name':'plan_visit','description':'Propose a date-aware itinerary within a total group budget, with travel time and capacity checks. No reservation is made.','inputSchema':schema({'visitor_id':VISITOR,'day':STR,'people':PEOPLE,'budget_eur':{'type':'number','minimum':0,'maximum':10000},'interests':{'type':'array','items':{'type':'string','enum':['art','nature','science','history','indoor','outdoor','quiet','active','family']},'maxItems':9},'start_time':STR,'accessible':{'type':'boolean'}},['visitor_id','day','people','budget_eur','interests']),'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}},
 {'name':'confirm_plan','description':'Record an explicitly confirmed demo reservation atomically; recheck capacity and prevent duplicate confirmation. No real booking or payment.','inputSchema':schema({'visitor_id':VISITOR,'plan_id':STR,'confirmed':{'type':'boolean'}},['visitor_id','plan_id','confirmed']),'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}},
 {'name':'list_reservations','description':'Retrieve only the supplied visitor session’s persisted demo reservations.','inputSchema':schema({'visitor_id':VISITOR},['visitor_id']),'annotations':{'readOnlyHint':True,'openWorldHint':False}},
 {'name':'cancel_reservation','description':'Cancel an explicitly confirmed demo reservation and release its places; safe to repeat.','inputSchema':schema({'visitor_id':VISITOR,'reservation_id':STR,'confirmed':{'type':'boolean'}},['visitor_id','reservation_id','confirmed']),'annotations':{'readOnlyHint':False,'destructiveHint':True,'idempotentHint':True,'openWorldHint':False}},
]
FUNCTIONS={f.__name__:f for f in [list_attractions,plan_visit,confirm_plan,list_reservations,cancel_reservation]}
def execute_tool(name,args):
 definition=next((t for t in TOOLS if t['name']==name),None)
 if not definition:raise ValueError('Unknown tool.')
 validate(args,definition['inputSchema']);return FUNCTIONS[name](**args)
class McpClient:
 def __init__(self):
  self.sid=None;self.sequence=0;self.trace=[];self.call('initialize',{'protocolVersion':PROTOCOL,'capabilities':{},'clientInfo':{'name':'visitorplan-simulated-assistant','version':'1.0.0'}});self.call('notifications/initialized',{},notification=True);self.call('tools/list',{})
 def call(self,method,params,notification=False):
  self.sequence+=1;body={'jsonrpc':'2.0','method':method,'params':params}
  if not notification:body['id']=self.sequence
  headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':PROTOCOL}
  if self.sid:headers['MCP-Session-Id']=self.sid
  req=urllib.request.Request('http://127.0.0.1:'+str(PORT)+'/mcp',data=json.dumps(body).encode(),headers=headers)
  with urllib.request.urlopen(req,timeout=15) as r:
   if r.headers.get('MCP-Session-Id'):self.sid=r.headers['MCP-Session-Id']
   result=json.load(r) if r.code!=202 else {}
  if result.get('error'):raise ValueError(result['error']['message'])
  return result.get('result',{})
 def tool(self,name,args,title):
  result=self.call('tools/call',{'name':name,'arguments':args})
  if result.get('isError'):raise ValueError(result['content'][0]['text'])
  self.trace.append({'title':title,'status':'completed'});return result['structuredContent']
 def close(self):
  if self.sid:
   req=urllib.request.Request('http://127.0.0.1:'+str(PORT)+'/mcp',method='DELETE',headers={'MCP-Session-Id':self.sid,'MCP-Protocol-Version':PROTOCOL})
   with urllib.request.urlopen(req,timeout=5):pass
def interpret_message(message,preferences):
 key=os.environ.get('GEMINI_API_KEY')
 if not key:return preferences,'structured preferences'
 fields={'day':{'type':'STRING'},'people':{'type':'INTEGER'},'budget_eur':{'type':'NUMBER'},'interests':{'type':'ARRAY','items':{'type':'STRING'}},'start_time':{'type':'STRING'},'accessible':{'type':'BOOLEAN'}}
 instruction='Interpret visitor planning preferences. Return JSON only. Never reserve or cancel anything. Resolve dates using Madrid today '+datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat()+'. Keep unmentioned values from supplied preferences. Allowed interests: art,nature,science,history,indoor,outdoor,quiet,active,family. budget_eur is the TOTAL group budget, not per-person. Supplied preferences: '+json.dumps(preferences)+'. Visitor message: '+message
 body={'contents':[{'parts':[{'text':instruction}]}],'generationConfig':{'maxOutputTokens':500,'responseMimeType':'application/json','responseSchema':{'type':'OBJECT','properties':fields,'required':list(fields)}}}
 req=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+os.environ.get('GEMINI_MODEL','gemini-3.1-flash-lite')+':generateContent',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','x-goog-api-key':key})
 try:
  with urllib.request.urlopen(req,timeout=30) as r:answer=json.load(r)
  text=''.join(p.get('text','') for p in answer['candidates'][0]['content']['parts']);parsed=json.loads(text);return parsed,'live language interpretation'
 except (urllib.error.URLError,ValueError,KeyError,IndexError):return preferences,'structured preferences; language service currently unavailable'
def agent(visitor,data):
 action=data.get('action','plan');client=McpClient()
 try:
  if action=='plan':
   prefs={k:data.get(k,v) for k,v in {'day':(datetime.now(ZoneInfo('Europe/Madrid')).date()+timedelta(days=1)).isoformat(),'people':2,'budget_eur':60,'interests':['art','nature'],'start_time':'11:30','accessible':False}.items()};mode='structured preferences'
   if data.get('message','').strip():prefs,mode=interpret_message(data['message'].strip(),prefs)
   client.tool('list_attractions',{'day':prefs['day'],'people':prefs['people'],'accessible':prefs['accessible']},'Checked current places and accessibility')
   result=client.tool('plan_visit',dict(prefs,visitor_id=visitor),'Built a route within your group budget')
   return {'kind':'plan','plan':result,'interpretation':mode,'steps':client.trace,'reply':('Here is a proposed demo itinerary. Review the date, group size and total before confirming.' if result['available'] else result['message'])}
  if action=='confirm':
   result=client.tool('confirm_plan',{'visitor_id':visitor,'plan_id':data.get('plan_id',''),'confirmed':data.get('confirmed') is True},'Rechecked places and recorded your confirmation')
   return {'kind':'reservation','reservation':result,'steps':client.trace,'reply':result['message']}
  if action=='cancel':
   result=client.tool('cancel_reservation',{'visitor_id':visitor,'reservation_id':data.get('reservation_id',''),'confirmed':data.get('confirmed') is True},'Cancelled the demo reservation and released its places')
   return {'kind':'cancelled','reservation':result,'steps':client.trace,'reply':result['message']}
  raise ValueError('Choose planning, confirmation or cancellation.')
 finally:client.close()

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def cookies(self):return dict(x.strip().split('=',1) for x in self.headers.get('Cookie','').split(';') if '=' in x)
 def visitor(self):
  value=self.cookies().get('visitorplan_session','')
  try:return valid_visitor(value)
  except ValueError:return None
 def send(self,status,data,kind='application/json; charset=utf-8',headers=None):
  b=json.dumps(data,ensure_ascii=False).encode() if kind.startswith('application/json') else (data.encode() if isinstance(data,str) else data)
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(b)
 def rpc(self,rid,result=None,error=None,headers=None):
  data={'jsonrpc':'2.0','id':rid};data['error' if error else 'result']=error or result;self.send(200,data,headers=headers)
 def origin_valid(self):
  origin=self.headers.get('Origin');return not origin or origin in ORIGINS
 def do_GET(self):
  path=urllib.parse.urlparse(self.path).path
  if path=='/':
   visitor=self.visitor() or str(uuid.uuid4());return self.send(200,(ROOT/'index.html').read_text(),'text/html; charset=utf-8',{'Set-Cookie':f'visitorplan_session={visitor}; Path=/; HttpOnly; SameSite=Lax'})
  if path=='/health':return self.send(200,{'status':'ok','application':'VisitorPlan','experience':'simulated-alexa','mcpProtocol':PROTOCOL})
  if path=='/mcp':
   if not self.origin_valid():return self.send(403,{'error':'Origin not allowed.'})
   return self.send(405,{'error':'This MCP service returns JSON responses; an SSE listening stream is not provided.'},headers={'Allow':'POST, DELETE'})
  if path=='/api/state':
   visitor=self.visitor()
   if not visitor:return self.send(403,{'error':'Open the visitor demo to begin.'})
   result=list_reservations(visitor)
   with connection() as c:result['plans']=[json.loads(r['data']) for r in c.execute('SELECT data FROM plans WHERE visitor=? ORDER BY created DESC LIMIT 5',(visitor,))]
   result['today']=datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat();return self.send(200,result)
  self.send(404,{'error':'Not found.'})
 def do_DELETE(self):
  if urllib.parse.urlparse(self.path).path!='/mcp':return self.send(404,{'error':'Not found.'})
  if not self.origin_valid():return self.send(403,{'error':'Origin not allowed.'})
  sid=self.headers.get('MCP-Session-Id')
  if sid not in MCP_SESSIONS:return self.send(404,{'error':'MCP session not found.'})
  MCP_SESSIONS.pop(sid,None);self.send(204,'','text/plain')
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path
  if not self.origin_valid():return self.send(403,{'error':'Origin not allowed.'})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if size>200000:raise ValueError('The request is too large.')
   data=json.loads(self.rfile.read(size) or '{}')
   if not isinstance(data,dict):raise ValueError('JSON object required.')
  except (ValueError,TypeError):return self.send(400,{'error':'Send a valid JSON request.'})
  if path=='/api/agent':
   visitor=self.visitor()
   if not visitor:return self.send(403,{'error':'Open the visitor demo to begin.'})
   try:return self.send(200,agent(visitor,data))
   except (ValueError,ValidationError) as e:return self.send(400,{'error':e.message if isinstance(e,ValidationError) else str(e)})
   except urllib.error.URLError:return self.send(503,{'error':'The planning service is temporarily unavailable. Please try again.'})
  if path!='/mcp':return self.send(404,{'error':'Not found.'})
  rid=data.get('id');method=data.get('method');params=data.get('params',{})
  if data.get('jsonrpc')!='2.0' or not isinstance(method,str):return self.rpc(rid,error={'code':-32600,'message':'Invalid JSON-RPC request.'})
  if not isinstance(params,dict):return self.rpc(rid,error={'code':-32602,'message':'Parameters must be an object.'})
  accepts=self.headers.get('Accept','')
  if 'application/json' not in accepts or 'text/event-stream' not in accepts:return self.send(406,{'error':'Accept both application/json and text/event-stream.'})
  version=self.headers.get('MCP-Protocol-Version',PROTOCOL)
  if version!=PROTOCOL:return self.send(400,{'error':'Unsupported MCP protocol version.'})
  if method=='initialize':
   if not isinstance(params.get('capabilities'),dict) or not isinstance(params.get('clientInfo'),dict) or not isinstance(params['clientInfo'].get('name'),str) or not isinstance(params['clientInfo'].get('version'),str):return self.rpc(rid,error={'code':-32602,'message':'Initialization requires capabilities and client name/version.'})
   if params.get('protocolVersion')!=PROTOCOL:return self.rpc(rid,error={'code':-32602,'message':'This service supports MCP 2025-11-25.'})
   sid=secrets.token_urlsafe(30);MCP_SESSIONS[sid]={'initialized':False,'created':time.time(),'calls':[]}
   return self.rpc(rid,{'protocolVersion':PROTOCOL,'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'visitorplan-mcp','version':'1.0.0'},'instructions':'Fictional-demo attractions only. Ask for explicit confirmation before confirming or cancelling reservations.'},headers={'MCP-Session-Id':sid})
  sid=self.headers.get('MCP-Session-Id');session=MCP_SESSIONS.get(sid)
  if not sid:return self.send(400,{'error':'MCP-Session-Id is required.'})
  if not session or time.time()-session['created']>3600:return self.send(404,{'error':'MCP session not found or expired.'})
  if method=='notifications/initialized':session['initialized']=True;return self.send(202,'','text/plain')
  if 'id' not in data:return self.send(202,'','text/plain')
  if method=='ping':return self.rpc(rid,{})
  if not session['initialized']:return self.rpc(rid,error={'code':-32000,'message':'Initialize the MCP session first.'})
  if method=='tools/list':return self.rpc(rid,{'tools':TOOLS})
  if method=='tools/call':
   if not isinstance(params.get('name'),str) or not isinstance(params.get('arguments',{}),dict):return self.rpc(rid,error={'code':-32602,'message':'A tool name and argument object are required.'})
   if params['name'] not in FUNCTIONS:return self.rpc(rid,error={'code':-32602,'message':'Unknown tool.'})
   session['calls']=[t for t in session['calls'] if time.time()-t<60]
   if len(session['calls'])>=120:return self.rpc(rid,error={'code':-32000,'message':'Tool request limit reached. Please retry shortly.'})
   session['calls'].append(time.time())
   try:
    result=execute_tool(params.get('name'),params.get('arguments',{}));return self.rpc(rid,{'content':[{'type':'text','text':json.dumps(result)}],'structuredContent':result,'isError':False})
   except (ValueError,ValidationError) as e:return self.rpc(rid,{'content':[{'type':'text','text':e.message if isinstance(e,ValidationError) else str(e)}],'isError':True})
  self.rpc(rid,error={'code':-32601,'message':'Method not found.'})
if __name__=='__main__':
 initialize_db();ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
