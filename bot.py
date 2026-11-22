import os, sqlite3, logging
from datetime import datetime, timezone
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

TOKEN=os.getenv('BOT_TOKEN','').strip(); ADMINS={int(x) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit()}
API_URL=os.getenv('PROVIDER_API_URL','https://my.smmgen.com/api/v2').strip(); API_KEY=os.getenv('PROVIDER_API_KEY','').strip(); DB=os.getenv('DB_PATH','noyon_smm.sqlite3')
logging.basicConfig(level=logging.INFO); log=logging.getLogger('noyon')
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def conn():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c
def init():
 with conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,username TEXT,balance REAL DEFAULT 0,banned INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
  CREATE TABLE IF NOT EXISTS cats(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,enabled INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS services(id INTEGER PRIMARY KEY AUTOINCREMENT,cat_id INTEGER,name TEXT,provider_id TEXT,rate REAL,minq INTEGER,maxq INTEGER,description TEXT DEFAULT '',enabled INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,service_id INTEGER,provider_id TEXT,target TEXT,qty INTEGER,charge REAL,status TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,amount REAL,proof TEXT,status TEXT DEFAULT 'Pending',created TEXT);
  CREATE TABLE IF NOT EXISTS tx(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,amount REAL,note TEXT,created TEXT);
  CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,reward REAL,enabled INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,user_id INTEGER,proof TEXT,status TEXT DEFAULT 'Pending',UNIQUE(task_id,user_id));
  CREATE TABLE IF NOT EXISTS gifts(code TEXT PRIMARY KEY,amount REAL,limit_uses INTEGER,uses INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS gift_used(code TEXT,user_id INTEGER,PRIMARY KEY(code,user_id));
  CREATE TABLE IF NOT EXISTS referrals(user_id INTEGER PRIMARY KEY,referrer_id INTEGER,bonus_paid INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS channels(id INTEGER PRIMARY KEY AUTOINCREMENT,chat TEXT,kind TEXT,UNIQUE(chat,kind));
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
  CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,status TEXT DEFAULT 'Open');''')
  for a in ADMINS: c.execute('INSERT OR IGNORE INTO admins(id) VALUES(?)',(a,))
  for k,v in {'min_deposit':'50','markup':'0','new_orders':'1','maintenance':'0','force_join':'0','ref_bonus':'0','live_order':'1','live_deposit':'1','live_task':'1'}.items(): c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)',(k,v))
def isadmin(uid):
 if uid in ADMINS:return True
 with conn() as c:return c.execute('SELECT 1 FROM admins WHERE id=?',(uid,)).fetchone() is not None
def setting(k,d=''):
 with conn() as c:r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone();return r['value'] if r else d
def setv(k,v):
 with conn() as c:c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
def cash(n):return f'৳{float(n):,.2f}'
def markup(rows):return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=d) for t,d in row] for row in rows])
def menu(admin=False):
 r=[[('🛍️ New Order','order'),('🔎 Track Order','orders')],[('📦 My Orders','orders'),('📜 Order History','orders')],[('💰 My Wallet','wallet'),('➕ Deposit','deposit')],[('🎁 Gift Code','gift'),('👥 Referral','ref')],[('🎯 Earn Tasks','tasks'),('🧾 My Submissions','mysub')],[('📚 Services','cats'),('🧮 Price Calculator','calc')],[('🆘 Support','support'),('⚠️ Report Problem','report')],[('👤 My Profile','profile'),('❓ FAQ','faq')]]
 if admin:r.append([('👑 Admin Panel','admin')])
 return markup(r)
def amenu():return markup([[('📊 Dashboard','dash'),('👥 Users','users')],[('📂 Categories','catsadmin'),('⚙️ Services','svcadmin')],[('🛍️ Orders','ordersadmin'),('💳 Deposits','deposits')],[('🎯 Tasks','tasksadmin'),('🎁 Gift Codes','giftsadmin')],[('📢 Live Channels','channels'),('🔒 Force Join','force')],[('🔌 Provider API Test','apitest'),('⚙️ Settings','settings')],[('🆘 Tickets','tickets'),('👮 Admin Managers','admins')],[('⬅️ Main Menu','home')]])
def flow(ctx,action,data=None):ctx.user_data['flow']={'a':action,'d':data or {}}
def cancel():return markup([[('❌ Cancel','cancel'),('🏠 Home','home')]])
async def notify(ctx,text,kind=None):
 key={'order':'live_order','deposit':'live_deposit','task':'live_task'}.get(kind)
 if key and setting(key,'1')!='1':return
 with conn() as c: admins={r['id'] for r in c.execute('SELECT id FROM admins')}; chans=[r['chat'] for r in c.execute("SELECT chat FROM channels WHERE kind='live'")]
 for a in admins:
  try:await ctx.bot.send_message(a,text)
  except Exception:pass
 for ch in chans:
  try:await ctx.bot.send_message(ch,text)
  except Exception:log.exception('channel notification failed')
async def force_join_ok(ctx, uid):
 if setting('force_join','0') != '1': return True
 with conn() as c: rows=c.execute("SELECT chat FROM channels WHERE kind='force'").fetchall()
 if not rows: return True
 for row in rows:
  chat=row['chat'].strip()
  try:
   member=await ctx.bot.get_chat_member(chat, uid)
   if member.status in ('left','kicked'): return False
  except Exception:
   # If the bot cannot check a channel, fail closed rather than pretending verification succeeded.
   return False
 return True

def force_keyboard():
 with conn() as c: rows=c.execute("SELECT chat FROM channels WHERE kind='force'").fetchall()
 buttons=[]
 for row in rows:
  chat=row['chat'].strip()
  url='https://t.me/'+chat.lstrip('@') if chat.startswith('@') else None
  if url: buttons.append([InlineKeyboardButton('📢 Join '+chat, url=url)])
 buttons.append([InlineKeyboardButton('✅ Verify Membership', callback_data='verifyjoin')])
 return InlineKeyboardMarkup(buttons)

async def start(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
 x=u.effective_user
 with conn() as c:
  existed=c.execute('SELECT 1 FROM users WHERE id=?',(x.id,)).fetchone()
  c.execute('INSERT INTO users(id,name,username) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,username=excluded.username',(x.id,x.full_name,x.username))
  if not existed and ctx.args and ctx.args[0].startswith('ref_'):
   try:
    refid=int(ctx.args[0][4:])
    if refid != x.id and c.execute('SELECT 1 FROM users WHERE id=?',(refid,)).fetchone():
     c.execute('INSERT OR IGNORE INTO referrals(user_id,referrer_id) VALUES(?,?)',(x.id,refid))
     bonus=float(setting('ref_bonus','0'))
     if bonus>0 and c.execute('SELECT changes()').fetchone()[0]:
      c.execute('UPDATE users SET balance=balance+? WHERE id=?',(bonus,refid))
      c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(refid,bonus,f'Referral bonus for {x.id}',now()))
   except ValueError: pass
 if banned(x.id):return await u.effective_message.reply_text('⛔ Account disabled. Support-এ যোগাযোগ করো।')
 if setting('maintenance','0')=='1' and not isadmin(x.id):return await u.effective_message.reply_text('🛠️ Maintenance চলছে।')
 if setting('force_join','0')=='1' and not await force_join_ok(ctx,x.id):
  return await u.effective_message.reply_text('🔒 আগে নিচের প্রয়োজনীয় Channel-গুলো Join করো, তারপর Verify চাপো।',reply_markup=force_keyboard())
 await u.effective_message.reply_text(f'💎 NOYON SMM ULTRA PRO\n\nস্বাগতম, {x.first_name}!\nনিচের বাটন থেকে কাজ বেছে নাও।',reply_markup=menu(isadmin(x.id)))
async def idcmd(u,ctx):await u.effective_message.reply_text(f'Your Telegram ID: {u.effective_user.id}')
def banned(uid):
 with conn() as c:r=c.execute('SELECT banned FROM users WHERE id=?',(uid,)).fetchone();return bool(r and r['banned'])
async def send(u,text,kb=None):
 if u.callback_query:await u.callback_query.edit_message_text(text,reply_markup=kb)
 else:await u.effective_message.reply_text(text,reply_markup=kb)
async def cb(u,ctx):
 q=u.callback_query;await q.answer();uid=q.from_user.id;d=q.data
 if d=='cancel':ctx.user_data.pop('flow',None);return await send(u,'কাজ বাতিল হয়েছে।',menu(isadmin(uid)))
 if d=='home':ctx.user_data.pop('flow',None);return await send(u,'💎 NOYON SMM ULTRA PRO\nমেনু থেকে বেছে নাও।',menu(isadmin(uid)))
 if d=='verifyjoin':
  if await force_join_ok(ctx,uid):return await send(u,'✅ Membership verified! Main Menu খুলে গেছে।',menu(isadmin(uid)))
  return await send(u,'❌ সব প্রয়োজনীয় Channel Join হয়নি অথবা Bot-এর permission নেই।',force_keyboard())
 if d=='admin':
  if not isadmin(uid):return await q.answer('Admin only',show_alert=True)
  return await send(u,'👑 Master Admin Panel',amenu())
 if d=='profile':
  with conn() as c:r=c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  return await send(u,f"👤 Profile\nID: {uid}\nName: {r['name']}\nUsername: @{r['username'] or '-'}\nBalance: {cash(r['balance'])}",markup([[('⬅️ Back','home')]]))
 if d=='wallet':
  with conn() as c:r=c.execute('SELECT balance FROM users WHERE id=?',(uid,)).fetchone();tx=c.execute('SELECT amount,note FROM tx WHERE user_id=? ORDER BY id DESC LIMIT 5',(uid,)).fetchall()
  return await send(u,f"💰 Wallet: {cash(r['balance'])}\n"+'\n'.join(f"{cash(x['amount'])} — {x['note']}" for x in tx),markup([[('➕ Deposit','deposit'),('🎁 Gift Code','gift')],[('⬅️ Back','home')]]))
 if d in ('cats','order'):
  with conn() as c:rs=c.execute('SELECT id,name FROM cats WHERE enabled=1 ORDER BY id').fetchall()
  return await send(u,'📂 Category বেছে নাও',markup([[(r['name'],f'cat:{r["id"]}')] for r in rs]+[[('⬅️ Back','home')]]))
 if d.startswith('cat:'):
  with conn() as c:rs=c.execute('SELECT id,name,rate FROM services WHERE cat_id=? AND enabled=1',(int(d.split(':')[1]),)).fetchall()
  return await send(u,'⚙️ Service বেছে নাও',markup([[(f"{r['name']} · {cash(r['rate'])}/1000",f'svc:{r["id"]}')] for r in rs]+[[('⬅️ Categories','cats')]]))
 if d.startswith('svc:'):
  sid=int(d.split(':')[1])
  with conn() as c:r=c.execute('SELECT * FROM services WHERE id=?',(sid,)).fetchone()
  return await send(u,f"⚙️ {r['name']}\nRate/1000: {cash(r['rate'])}\nMin: {r['minq']} · Max: {r['maxq']}\n{r['description']}",markup([[('🛍️ Order This',f'buy:{sid}')],[('⬅️ Back','cats')]]))
 if d.startswith('buy:'):
  flow(ctx,'link',{'sid':int(d.split(':')[1])});return await send(u,'🔗 Target URL পাঠাও।',cancel())
 if d in ('orders','ordersadmin'):
  if d=='ordersadmin' and not isadmin(uid):return await q.answer('Admin only',show_alert=True)
  with conn() as c:rs=c.execute('SELECT id,user_id,status,qty,charge,target FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10',(uid,)).fetchall() if d=='orders' else c.execute('SELECT id,user_id,status,qty,charge,target FROM orders ORDER BY id DESC LIMIT 20').fetchall()
  return await send(u,'📦 Orders\n'+('\n'.join(f"#{r['id']} User:{r['user_id']} {r['status']} Qty:{r['qty']} {cash(r['charge'])}" for r in rs) if rs else 'কোনো Order নেই।'),menu(isadmin(uid)))
 if d=='deposit':flow(ctx,'depamount');return await send(u,f"➕ Deposit\nMinimum: {cash(setting('min_deposit','50'))}\nAmount লিখে পাঠাও।",cancel())
 if d=='gift':flow(ctx,'gift');return await send(u,'🎁 Gift Code পাঠাও।',cancel())
 if d=='ref':
  with conn() as c:bot=ctx.bot.username or 'YOUR_BOT';n=c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id=?',(uid,)).fetchone()[0]
  return await send(u,f'👥 Referral Link: https://t.me/{bot}?start=ref_{uid}\nSuccessful referrals: {n}\nReferral bonus setting: {cash(setting("ref_bonus","0"))}',markup([[('⬅️ Back','home')]]))
 if d=='tasks':
  with conn() as c:rs=c.execute('SELECT * FROM tasks WHERE enabled=1').fetchall()
  return await send(u,'🎯 Tasks',markup([[(f"{r['title']} · {cash(r['reward'])}",f'task:{r["id"]}')] for r in rs]+[[('⬅️ Back','home')]]))
 if d.startswith('task:'):
  tid=int(d.split(':')[1]);flow(ctx,'proof',{'tid':tid});return await send(u,'Task proof text বা screenshot পাঠাও।',cancel())
 if d=='mysub':
  with conn() as c:rs=c.execute('SELECT s.id,t.title,s.status FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.user_id=?',(uid,)).fetchall()
  return await send(u,'🧾 My Submissions\n'+('\n'.join(f"#{r['id']} {r['title']} — {r['status']}" for r in rs) if rs else 'কোনো Submission নেই।'),menu(isadmin(uid)))
 if d in ('support','report'):flow(ctx,'ticket',{'report':d=='report'});return await send(u,'সমস্যা লিখে পাঠাও।',cancel())
 if d=='faq':return await send(u,'❓ FAQ\nOrder দিতে Service নির্বাচন করো। Deposit Admin যাচাই করবে। Provider error হলে order success দেখানো হবে না।',menu(isadmin(uid)))
 if d=='dash' and isadmin(uid):
  with conn() as c:
   users_count=c.execute('SELECT count(*) FROM users').fetchone()[0]
   orders_count=c.execute('SELECT count(*) FROM orders').fetchone()[0]
   pending_count=c.execute("SELECT count(*) FROM deposits WHERE status='Pending'").fetchone()[0]
  txt=f"Users: {users_count}\nOrders: {orders_count}\nPending Deposits: {pending_count}"
  return await send(u,'📊 Dashboard\n'+txt,amenu())
 if d in ('catsadmin','svcadmin','users','deposits','tasksadmin','giftsadmin','channels','force','settings','tickets','admins'):
  if not isadmin(uid):return await q.answer('Admin only',show_alert=True)
  options={'catsadmin':[[('➕ Add Category','addcat')],[('📋 List Categories','catlist')]],'svcadmin':[[('➕ Add Service','addsvc')],[('📋 List Services','svclist')]],'users':[[('🔎 Search User ID','searchuser')],[('📋 Recent Users','userlist')]],'deposits':[[('📋 Pending Deposits','deplist')]],'tasksadmin':[[('➕ Create Task','addtask')],[('📋 Review Submissions','sublist')]],'giftsadmin':[[('➕ Create Gift Code','addgift')]],'channels':[[('➕ Add Live Channel','addchannel')],[('📋 List Channels','chlist')],[('🔔 Notification Toggle','notiftoggle')]],'force':[[('➕ Add Force Join Channel','addforce')],[('📋 List/Remove Channels','forcelist')],[('🔘 Toggle Force Join','forcetoggle')]],'settings':[[('💵 Minimum Deposit','minset'),('📈 Markup %','markupset')],[('🎁 Referral Bonus','refbonusset')],[('🔘 New Orders On/Off','ordertoggle'),('🛠️ Maintenance','maintoggle')]],'tickets':[[('📋 Open Tickets','ticketlist')]],'admins':[[('➕ Add Admin','addadmin')],[('➖ Remove Admin','removeadmin')]]}
  return await send(u,'⚙️ Admin Manager',markup(options[d]+[[('⬅️ Admin Menu','admin')]]))
 if d in ('addcat','addsvc','searchuser','addtask','addgift','addchannel','addforce','minset','markupset','refbonusset','addadmin','removeadmin') and isadmin(uid):
  if d=='addsvc':
   with conn() as c:rs=c.execute('SELECT id,name FROM cats').fetchall()
   return await send(u,'Category বেছে নাও',markup([[(r['name'],f'addsvc_cat:{r["id"]}')] for r in rs]+[[('➕ Add Category','addcat')]]))
  flow(ctx,d);return await send(u,{'addcat':'Category name পাঠাও।','searchuser':'User ID পাঠাও।','addtask':'Title | Reward | Description পাঠাও।','addgift':'CODE | AMOUNT | MAX_USES পাঠাও।','addchannel':'@channel বা chat ID পাঠাও।','addforce':'@channel বা chat ID পাঠাও।','minset':'Minimum Deposit amount পাঠাও।','markupset':'Markup percent পাঠাও।','refbonusset':'প্রতি referral bonus amount পাঠাও।','addadmin':'Admin Telegram ID পাঠাও।','removeadmin':'যে Admin বাদ দেবে তার ID পাঠাও।'}.get(d,'তথ্য পাঠাও।'),cancel())
 if d.startswith('addsvc_cat:') and isadmin(uid):flow(ctx,'addsvc',{'cat':int(d.split(':')[1])});return await send(u,'Name | Provider ID | Rate/1000 | Min | Max | Description পাঠাও।',cancel())
 if d=='catlist' and isadmin(uid):
  with conn() as c:rs=c.execute('SELECT * FROM cats').fetchall()
  return await send(u,'📂 Categories',markup([[(f"#{r['id']} {r['name']}",f'catmanage:{r["id"]}')] for r in rs]+[[('➕ Add Category','addcat')],[('⬅️ Admin','admin')]]))
 if d.startswith('catmanage:') and isadmin(uid):return await send(u,'Category action',markup([[('✏️ Rename',f'catrename:{d.split(":")[1]}'),('🗑️ Delete',f'catdelete:{d.split(":")[1]}')]]))
 if d.startswith(('catrename:','catdelete:')) and isadmin(uid):
  cid=int(d.split(':')[1])
  if d.startswith('catdelete:'):
   with conn() as c:c.execute('DELETE FROM services WHERE cat_id=?',(cid,));c.execute('DELETE FROM cats WHERE id=?',(cid,))
   return await send(u,'Category deleted.',amenu())
  flow(ctx,'catrename',{'id':cid});return await send(u,'নতুন নাম পাঠাও।',cancel())
 if d=='svclist' and isadmin(uid):
  with conn() as c:rs=c.execute('SELECT s.*,c.name cat FROM services s JOIN cats c ON c.id=s.cat_id ORDER BY s.id DESC LIMIT 50').fetchall()
  return await send(u,'⚙️ Services',markup([[(f"#{r['id']} {r['name']} · {cash(r['rate'])}",f'svcmanage:{r["id"]}')] for r in rs]+[[('➕ Add Service','addsvc')]]))
 if d.startswith('svcmanage:') and isadmin(uid):return await send(u,'Service actions',markup([[('✏️ Edit',f'svcedit:{d.split(":")[1]}'),('🔘 Enable/Disable',f'svctoggle:{d.split(":")[1]}')],[('🗑️ Delete',f'svcdelete:{d.split(":")[1]}')]]))
 if d.startswith('svcedit:') and isadmin(uid):flow(ctx,'svcedit',{'id':int(d.split(':')[1])});return await send(u,'Name | Provider ID | Rate/1000 | Min | Max | Description পাঠাও।',cancel())
 if d.startswith('svctoggle:') and isadmin(uid):
  with conn() as c:c.execute('UPDATE services SET enabled=1-enabled WHERE id=?',(int(d.split(':')[1]),))
  return await send(u,'Service status changed.',amenu())
 if d.startswith('svcdelete:') and isadmin(uid):
  with conn() as c:c.execute('DELETE FROM services WHERE id=?',(int(d.split(':')[1]),))
  return await send(u,'Service deleted.',amenu())
 if d=='userlist' and isadmin(uid):
  with conn() as c:rs=c.execute('SELECT * FROM users ORDER BY id DESC LIMIT 30').fetchall()
  return await send(u,'👥 Users\n'+'\n'.join(f"{r['id']} {r['name']} @{r['username'] or '-'} {cash(r['balance'])} {'BANNED' if r['banned'] else ''}" for r in rs),amenu())
 if d=='deplist' and isadmin(uid):
  with conn() as c:rs=c.execute("SELECT * FROM deposits WHERE status='Pending'").fetchall()
  return await send(u,'Pending Deposits',markup([[(f"#{r['id']} User {r['user_id']} {cash(r['amount'])}",f'dep:{r["id"]}')] for r in rs]+[[('⬅️ Admin','admin')]]))
 if d.startswith('dep:') and isadmin(uid):
  did=int(d.split(':')[1]);return await send(u,'Deposit action',markup([[('✅ Approve',f'depok:{did}'),('❌ Reject',f'depno:{did}')]]))
 if d.startswith(('depok:','depno:')) and isadmin(uid):
  did=int(d.split(':')[1]);ok=d.startswith('depok:')
  with conn() as c:
   r=c.execute("SELECT * FROM deposits WHERE id=? AND status='Pending'",(did,)).fetchone()
   if not r:return await send(u,'Already processed.',amenu())
   c.execute('UPDATE deposits SET status=? WHERE id=?',('Approved' if ok else 'Rejected',did))
   if ok:c.execute('UPDATE users SET balance=balance+? WHERE id=?',(r['amount'],r['user_id']));c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(r['user_id'],r['amount'],f'Deposit #{did}',now()))
  await notify(ctx,f"💳 Deposit #{did} {'Approved' if ok else 'Rejected'}",'deposit');return await send(u,'Deposit updated.',amenu())
 if d=='sublist' and isadmin(uid):
  with conn() as c:rs=c.execute("SELECT s.*,t.title FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.status='Pending'").fetchall()
  return await send(u,'Task Submissions',markup([[(f"#{r['id']} User {r['user_id']} {r['title']}",f'sub:{r["id"]}')] for r in rs]+[[('⬅️ Admin','admin')]]))
 if d.startswith('sub:') and isadmin(uid):return await send(u,'Submission action',markup([[('✅ Approve',f'subok:{d.split(":")[1]}'),('❌ Reject',f'subno:{d.split(":")[1]}')]]))
 if d.startswith(('subok:','subno:')) and isadmin(uid):
  sid=int(d.split(':')[1]);ok=d.startswith('subok:')
  with conn() as c:
   r=c.execute("SELECT s.*,t.reward FROM submissions s JOIN tasks t ON t.id=s.task_id WHERE s.id=? AND s.status='Pending'",(sid,)).fetchone()
   if not r:return await send(u,'Already processed.',amenu())
   c.execute('UPDATE submissions SET status=? WHERE id=?',('Approved' if ok else 'Rejected',sid))
   if ok:c.execute('UPDATE users SET balance=balance+? WHERE id=?',(r['reward'],r['user_id']));c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(r['user_id'],r['reward'],f'Task reward #{sid}',now()))
  return await send(u,'Submission updated.',amenu())
 if d=='ticketlist' and isadmin(uid):
  with conn() as c:rs=c.execute("SELECT * FROM tickets WHERE status='Open' ORDER BY id DESC LIMIT 30").fetchall()
  return await send(u,'🆘 Open Tickets',markup([[(f'#{r["id"]} User {r["user_id"]}',f'ticketview:{r["id"]}')] for r in rs]+[[('⬅️ Admin','admin')]]))
 if d.startswith('ticketview:') and isadmin(uid):
  tid=int(d.split(':')[1])
  with conn() as c:r=c.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
  if not r:return await send(u,'Ticket not found.',amenu())
  return await send(u,f'Ticket #{tid}\nUser: {r["user_id"]}\nStatus: {r["status"]}\n\n{r["message"]}',markup([[('✉️ Reply',f'ticketreply:{tid}')],[('✅ Close Ticket',f'ticketclose:{tid}')],[('⬅️ Tickets','ticketlist')]]))
 if d.startswith('ticketreply:') and isadmin(uid):
  tid=int(d.split(':')[1])
  with conn() as c:r=c.execute('SELECT user_id FROM tickets WHERE id=?',(tid,)).fetchone()
  if not r:return await send(u,'Ticket not found.',amenu())
  flow(ctx,'reply',{'uid':r['user_id'],'tid':tid});return await send(u,'Reply message পাঠাও।',cancel())
 if d.startswith('ticketclose:') and isadmin(uid):
  with conn() as c:c.execute("UPDATE tickets SET status='Closed' WHERE id=?",(int(d.split(':')[1]),))
  return await send(u,'Ticket closed.',amenu())
 if d=='apitest' and isadmin(uid):
  if not API_KEY:return await send(u,'PROVIDER_API_KEY environment variable সেট করা নেই।',amenu())
  try:
   rr=requests.post(API_URL,data={'key':API_KEY,'action':'services'},timeout=15);rr.raise_for_status();v=rr.json()
   return await send(u,'API response received: '+('error: '+str(v['error']) if isinstance(v,dict) and v.get('error') else 'OK'),amenu())
  except Exception as e:return await send(u,'API test failed: '+str(e)[:180],amenu())
 if d in ('forcetoggle','ordertoggle','maintoggle','notiftoggle') and isadmin(uid):
  key={'forcetoggle':'force_join','ordertoggle':'new_orders','maintoggle':'maintenance','notiftoggle':'live_order'}[d];setv(key,'0' if setting(key,'0')=='1' else '1');return await send(u,f'✅ {key} এখন {"ON" if setting(key,"0")=="1" else "OFF"}',amenu())
 if d=='forcelist' and isadmin(uid):
  with conn() as c:rs=c.execute("SELECT * FROM channels WHERE kind='force'").fetchall()
  return await send(u,'🔒 Force Join Channels',markup([[(r['chat'],f'forcedel:{r["id"]}')] for r in rs]+[[('⬅️ Admin','admin')]]))
 if d.startswith('forcedel:') and isadmin(uid):
  with conn() as c:c.execute("DELETE FROM channels WHERE id=? AND kind='force'",(int(d.split(':')[1]),))
  return await send(u,'Force Join Channel removed.',amenu())
 if d=='chlist' and isadmin(uid):
  with conn() as c:rs=c.execute("SELECT * FROM channels WHERE kind='live'").fetchall()
  return await send(u,'Live Channels',markup([[(r['chat'],f'chdel:{r["id"]}')] for r in rs]+[[('⬅️ Admin','admin')]]))
 if d.startswith('chdel:') and isadmin(uid):
  with conn() as c:c.execute('DELETE FROM channels WHERE id=?',(int(d.split(':')[1]),))
  return await send(u,'Channel removed.',amenu())
 await send(u,'এই বাটনটি এখনো যুক্ত করা হয়নি।',menu(isadmin(uid)))
async def text_handler(u,ctx):
 x=u.effective_user;msg=u.effective_message;f=ctx.user_data.get('flow')
 if not f:return await msg.reply_text('মেনু থেকে বাটন বেছে নাও।',reply_markup=menu(isadmin(x.id)))
 a=f['a'];d=f.get('d',{});text=msg.text or msg.caption or '';proof=(f'photo_file_id:{msg.photo[-1].file_id} '+text) if msg.photo else text
 try:
  if a=='link':
   if not text.startswith(('http://','https://')):return await msg.reply_text('সঠিক URL দাও।',reply_markup=cancel())
   flow(ctx,'qty',{'sid':d['sid'],'target':text});return await msg.reply_text('Quantity পাঠাও।',reply_markup=cancel())
  if a=='qty':
   qty=int(text)
   with conn() as c:s=c.execute('SELECT * FROM services WHERE id=? AND enabled=1',(d['sid'],)).fetchone();user=c.execute('SELECT balance FROM users WHERE id=?',(x.id,)).fetchone()
   if not s:return await msg.reply_text('Service পাওয়া যায়নি।',reply_markup=menu(isadmin(x.id)))
   if qty<s['minq'] or qty>s['maxq']:return await msg.reply_text(f'Min {s["minq"]}, Max {s["maxq"]}',reply_markup=cancel())
   charge=round(qty*s['rate']/1000*(1+float(setting('markup','0'))/100),2)
   if setting('new_orders','1')!='1':return await msg.reply_text('New orders বন্ধ।',reply_markup=menu(isadmin(x.id)))
   if not user or user['balance']<charge:return await msg.reply_text(f'Balance কম। প্রয়োজন {cash(charge)}',reply_markup=menu(isadmin(x.id)))
   if not API_KEY or not s['provider_id']:return await msg.reply_text('Provider API Key বা Provider Service ID সেট করা নেই। টাকা কাটা হয়নি।',reply_markup=cancel())
   try:
    rr=requests.post(API_URL,data={'key':API_KEY,'action':'add','service':s['provider_id'],'link':d['target'],'quantity':qty},timeout=25);rr.raise_for_status();p=rr.json()
    if not isinstance(p,dict) or p.get('error') or not p.get('order'):return await msg.reply_text('Provider order গ্রহণ করেনি: '+str(p.get('error','Unexpected response'))[:150],reply_markup=cancel())
   except Exception as e:log.exception('provider order error');return await msg.reply_text('Provider API Error; টাকা কাটা হয়নি। '+str(e)[:120],reply_markup=cancel())
   with conn() as c:
    c.execute('BEGIN IMMEDIATE');b=c.execute('SELECT balance FROM users WHERE id=?',(x.id,)).fetchone()
    if not b or b['balance']<charge:c.rollback();return await msg.reply_text('Provider order তৈরি হয়েছে কিন্তু wallet balance বদলে গেছে; Admin-কে জানাও।',reply_markup=menu(isadmin(x.id)))
    c.execute('UPDATE users SET balance=balance-? WHERE id=?',(charge,x.id));cur=c.execute('INSERT INTO orders(user_id,service_id,provider_id,target,qty,charge,status,created) VALUES(?,?,?,?,?,?,?,?)',(x.id,d['sid'],str(p['order']),d['target'],qty,charge,'Pending',now()));oid=cur.lastrowid;c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(x.id,-charge,f'Order #{oid}',now()))
   ctx.user_data.pop('flow',None);await msg.reply_text(f'✅ Order Accepted\nOrder ID: #{oid}\nProvider ID: {p["order"]}\nCharge: {cash(charge)}',reply_markup=menu(isadmin(x.id)));await notify(ctx,f'🛍️ New Order #{oid}\nUser {x.id}\nProvider ID {p["order"]}\nQty {qty}\n{cash(charge)}','order');return
  if a=='depamount':
   amount=float(text)
   if amount<float(setting('min_deposit','50')):return await msg.reply_text('Minimum Deposit '+cash(setting('min_deposit','50')),reply_markup=cancel())
   flow(ctx,'depproof',{'amount':amount});return await msg.reply_text('Transaction ID / payment proof পাঠাও।',reply_markup=cancel())
  if a=='depproof':
   with conn() as c:cur=c.execute('INSERT INTO deposits(user_id,amount,proof,created) VALUES(?,?,?,?)',(x.id,d['amount'],proof,now()));did=cur.lastrowid
   ctx.user_data.pop('flow',None);await msg.reply_text(f'Deposit request #{did} জমা হয়েছে।',reply_markup=menu(isadmin(x.id)));await notify(ctx,f'➕ Deposit #{did} User {x.id} Amount {cash(d["amount"])} Proof {proof}','deposit');return
  if a=='gift':
   with conn() as c:
    c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM gifts WHERE code=?',(text.strip().upper(),)).fetchone()
    if not r or r['uses']>=r['limit_uses'] or c.execute('SELECT 1 FROM gift_used WHERE code=? AND user_id=?',(r['code'],x.id)).fetchone():c.rollback();return await msg.reply_text('Gift code invalid/used.',reply_markup=cancel())
    c.execute('INSERT INTO gift_used VALUES(?,?)',(r['code'],x.id));c.execute('UPDATE gifts SET uses=uses+1 WHERE code=?',(r['code'],));c.execute('UPDATE users SET balance=balance+? WHERE id=?',(r['amount'],x.id));c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(x.id,r['amount'],'Gift code '+r['code'],now()))
   ctx.user_data.pop('flow',None);return await msg.reply_text('🎉 Gift code সফল: '+cash(r['amount']),reply_markup=menu(isadmin(x.id)))
  if a=='proof':
   with conn() as c:c.execute('INSERT INTO submissions(task_id,user_id,proof) VALUES(?,?,?)',(d['tid'],x.id,proof))
   ctx.user_data.pop('flow',None);await msg.reply_text('Proof জমা হয়েছে।',reply_markup=menu(isadmin(x.id)));await notify(ctx,f'🎯 Task proof User {x.id}, Task {d["tid"]}: {proof}','task');return
  if a=='ticket':
   with conn() as c:cur=c.execute('INSERT INTO tickets(user_id,message) VALUES(?,?)',(x.id,('Report: ' if d.get('report') else '')+proof));tid=cur.lastrowid
   ctx.user_data.pop('flow',None);await msg.reply_text(f'Ticket #{tid} জমা হয়েছে।',reply_markup=menu(isadmin(x.id)));await notify(ctx,f'🆘 Ticket #{tid} User {x.id}: {proof}');return
  if not isadmin(x.id):return await msg.reply_text('অনুমতি নেই।',reply_markup=menu(False))
  if a=='addcat':
   if not text.strip():return await msg.reply_text('Category name খালি রাখা যাবে না।',reply_markup=cancel())
   with conn() as c:c.execute('INSERT INTO cats(name) VALUES(?)',(text.strip(),))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Category added.',reply_markup=amenu())
  if a=='catrename':
   with conn() as c:c.execute('UPDATE cats SET name=? WHERE id=?',(text.strip(),d['id']))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Category renamed.',reply_markup=amenu())
  if a in ('addsvc','svcedit'):
   p=[z.strip() for z in text.split('|')]
   if len(p)<5:return await msg.reply_text('Name | Provider ID | Rate/1000 | Min | Max | Description',reply_markup=cancel())
   name,pid,rate,mn,mx=p[:5];desc=' | '.join(p[5:]) if len(p)>5 else '';rate=float(rate);mn=int(mn);mx=int(mx)
   if rate<0 or mn<1 or mx<mn:return await msg.reply_text('Rate/Min/Max ভুল।',reply_markup=cancel())
   with conn() as c:
    if a=='addsvc':c.execute('INSERT INTO services(cat_id,name,provider_id,rate,minq,maxq,description) VALUES(?,?,?,?,?,?,?)',(d['cat'],name,pid,rate,mn,mx,desc))
    else:c.execute('UPDATE services SET name=?,provider_id=?,rate=?,minq=?,maxq=?,description=? WHERE id=?',(name,pid,rate,mn,mx,desc,d['id']))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Service saved.',reply_markup=amenu())
  if a=='searchuser':
   target=int(text)
   with conn() as c:r=c.execute('SELECT * FROM users WHERE id=?',(target,)).fetchone()
   if not r:return await msg.reply_text('User not found.',reply_markup=amenu())
   ctx.user_data.pop('flow',None);return await msg.reply_text(f"ID {r['id']}\n{r['name']} @{r['username'] or '-'}\nBalance {cash(r['balance'])}\nBanned {r['banned']}",reply_markup=markup([[('➕ Add Balance',f'baladd:{target}'),('➖ Cut Balance',f'balcut:{target}')],[('🔨 Ban/Unban',f'ban:{target}')]]))
  if a=='addtask':
   p=text.split('|',2)
   if len(p)<3:return await msg.reply_text('Title | Reward | Description',reply_markup=cancel())
   with conn() as c:c.execute('INSERT INTO tasks(title,reward,description) VALUES(?,?,?)',(p[0].strip(),float(p[1]),p[2].strip()))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Task created.',reply_markup=amenu())
  if a=='addgift':
   p=[z.strip() for z in text.split('|')]
   if len(p)!=3:return await msg.reply_text('CODE | AMOUNT | MAX_USES',reply_markup=cancel())
   with conn() as c:c.execute('INSERT INTO gifts(code,amount,limit_uses) VALUES(?,?,?)',(p[0].upper(),float(p[1]),int(p[2])))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Gift code created.',reply_markup=amenu())
  if a in ('addchannel','addforce'):
   with conn() as c:c.execute('INSERT OR IGNORE INTO channels(chat,kind) VALUES(?,?)',(text.strip(),'live' if a=='addchannel' else 'force'))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Channel saved; add bot as admin there.',reply_markup=amenu())
  if a in ('minset','markupset','refbonusset'):
   val=float(text)
   if val<0:return await msg.reply_text('Negative value allowed নয়।',reply_markup=cancel())
   setv({'minset':'min_deposit','markupset':'markup','refbonusset':'ref_bonus'}[a],val);ctx.user_data.pop('flow',None);return await msg.reply_text('Setting saved.',reply_markup=amenu())
  if a in ('addadmin','removeadmin'):
   target=int(text)
   with conn() as c:
    if a=='addadmin':c.execute('INSERT OR IGNORE INTO admins(id) VALUES(?)',(target,))
    elif target not in ADMINS:c.execute('DELETE FROM admins WHERE id=?',(target,))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Admin list updated.',reply_markup=amenu())
  if a=='balance':
   amount=float(text);delta=amount if d['mode']=='add' else -amount
   if amount<=0:return await msg.reply_text('Amount > 0 হতে হবে।',reply_markup=cancel())
   with conn() as c:
    c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT balance FROM users WHERE id=?',(d['uid'],)).fetchone()
    if not r or (delta<0 and r['balance']<amount):c.rollback();return await msg.reply_text('User নেই বা Balance যথেষ্ট নয়।',reply_markup=amenu())
    c.execute('UPDATE users SET balance=balance+? WHERE id=?',(delta,d['uid']));c.execute('INSERT INTO tx(user_id,amount,note,created) VALUES(?,?,?,?)',(d['uid'],delta,'Admin adjustment',now()))
   ctx.user_data.pop('flow',None);return await msg.reply_text('Balance updated.',reply_markup=amenu())
  if a=='reply':
   await ctx.bot.send_message(d['uid'],'🆘 Support reply: '+text);ctx.user_data.pop('flow',None);return await msg.reply_text('Reply sent.',reply_markup=amenu())
 except ValueError:return await msg.reply_text('❌ সঠিক সংখ্যা/ফরম্যাট দাও।',reply_markup=cancel())
 except sqlite3.IntegrityError:return await msg.reply_text('❌ Duplicate data বা invalid entry.',reply_markup=cancel())
 except Exception as e:log.exception('flow error');return await msg.reply_text('❌ Error: '+str(e)[:150],reply_markup=cancel())
async def admin_cb(u,ctx):
 q=u.callback_query;await q.answer();uid=q.from_user.id;d=q.data
 if not isadmin(uid):return await q.answer('Admin only',show_alert=True)
 if d.startswith(('baladd:','balcut:')):flow(ctx,'balance',{'uid':int(d.split(':')[1]),'mode':'add' if d.startswith('baladd:') else 'cut'});return await send(u,'Amount পাঠাও।',cancel())
 if d.startswith('ban:'):
  target=int(d.split(':')[1])
  with conn() as c:c.execute('UPDATE users SET banned=1-banned WHERE id=?',(target,))
  return await send(u,'User ban status changed.',amenu())
async def error_handler(u,ctx):log.exception('bot error',exc_info=ctx.error)
def main():
 if not TOKEN:raise SystemExit('BOT_TOKEN environment variable required. See README.txt')
 init();app=Application.builder().token(TOKEN).build();app.add_handler(CommandHandler('start',start));app.add_handler(CommandHandler('id',idcmd));app.add_handler(CallbackQueryHandler(admin_cb,pattern=r'^(baladd:|balcut:|ban:)'));app.add_handler(CallbackQueryHandler(cb));app.add_handler(MessageHandler((filters.TEXT|filters.PHOTO)&~filters.COMMAND,text_handler));app.add_error_handler(error_handler);app.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__=='__main__':main()