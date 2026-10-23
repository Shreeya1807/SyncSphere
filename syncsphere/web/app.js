const $ = (id) => document.getElementById(id)
let csrf, user, socket, room, invitation, pending = null
const seen = new Set()
function status(message) { $('status').textContent = message; $('status').style.display = 'block'; setTimeout(() => $('status').style.display = 'none', 7000) }
async function api(path, method = 'GET', body) {
  const response = await fetch('/api' + path, { method, headers: { 'X-CSRF-Token': csrf || '', ...(body instanceof FormData ? {} : {'Content-Type':'application/json'}) }, body: body ? body instanceof FormData ? body : JSON.stringify(body) : undefined })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || 'Request failed')
  return data
}
function guarded(fn) { return async event => { event?.preventDefault(); try { await fn(event) } catch(error) { status(error.message) } } }
function render(message) {
  if (seen.has(message.id)) return
  seen.add(message.id)
  const node = document.createElement('article'); node.className = 'message' + (message.sender === user ? ' mine' : '')
  node.dataset.sequence = message.sequence
  const label = document.createElement('small'); label.textContent = `${message.sender} · ${new Date(message.created * 1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})} · saved`
  const text = document.createElement('p'); text.textContent = message.text
  node.append(label, text)
  if (message.file_id) { const a = document.createElement('a'); a.href = '/api/files/' + encodeURIComponent(message.file_id); a.textContent = '↓ ' + message.file_name; node.append(a) }
  if (message.text) { const button = document.createElement('button'); button.textContent = 'Translate'; button.onclick = guarded(async () => { const target = prompt('Translate to language code (en, es, fr…)', 'en'); if (!target) return; const result = await api(`/rooms/${room}/translate`, 'POST', {message_id:message.id,target}); text.textContent = result.text; button.textContent = 'Show original'; button.onclick = () => {text.textContent = message.text} }); node.append(button) }
  const later = [...$('messages').children].find(n => Number(n.dataset.sequence) > message.sequence)
  $('messages').insertBefore(node, later || null)
  $('messages').scrollTop = $('messages').scrollHeight
}
async function openRoom(id) {
  room = id; seen.clear(); $('messages').replaceChildren()
  const data = await api('/rooms/' + id)
  invitation = data.invite
  $('room-title').textContent = data.name; $('room-detail').textContent = `${data.members.length} members · Private invite-only room`
  $('compose').hidden = false; $('copy-invite').hidden = false
  for (const message of data.messages) render(message)
  // Subscribe first, then replay: covers messages saved between initial fetch and subscription.
  socket.emit('join', {room:id}, async result => { if(result.ok && room === id) { try { const replay = await api('/rooms/' + id); if(room === id) replay.messages.forEach(render) } catch(e) { status(e.message) } } })
  document.querySelectorAll('#rooms button').forEach(n => n.classList.toggle('active', n.dataset.room === id))
}
async function loadRooms() {
  const data = await api('/rooms'); $('rooms').replaceChildren()
  for (const item of data.rooms) { const button = document.createElement('button'); button.textContent = '# ' + item.name; button.dataset.room = item.id; button.onclick = guarded(() => openRoom(item.id)); $('rooms').append(button) }
}
async function enter() {
  $('auth').hidden = true; $('workspace').hidden = false; $('logout').hidden = false
  socket?.disconnect(); socket = io({auth:{csrf}, transports:['websocket']})
  socket.on('connect', () => { $('connection').textContent = 'Connected · ' + user; if(room) openRoom(room).catch(e => status(e.message)) })
  socket.on('disconnect', () => $('connection').textContent = 'Reconnecting… messages stay saved')
  socket.on('connect_error', () => status('Connection unavailable. Refresh if your session expired.'))
  socket.on('message', data => { if(data.room === room) render(data.message) })
  await loadRooms()
}
$('auth-form').onsubmit = guarded(async event => { const result = await api('/' + event.submitter.value, 'POST', {username:$('username').value,password:$('password').value}); user = result.username; csrf = result.csrf; $('password').value = ''; await enter() })
$('create-room').onsubmit = guarded(async () => { const result = await api('/rooms','POST',{name:$('room-name').value}); $('room-name').value=''; await loadRooms(); await openRoom(result.id) })
$('join-room').onsubmit = guarded(async () => { const result = await api('/join','POST',{invite:$('invite').value.trim()}); $('invite').value=''; await loadRooms(); await openRoom(result.id) })
$('copy-invite').onclick = guarded(async () => { await navigator.clipboard.writeText(invitation); status('Invite copied. Share it only with your intended team.') })
$('logout').onclick = guarded(async () => { await api('/logout','POST',{}); socket?.disconnect(); location.reload() })
$('compose').onsubmit = guarded(async () => {
  if(!socket.connected) throw new Error('Wait for the connection before sending')
  const text = $('message').value.trim(), file = $('file').files[0]
  if(!text && !file) return
  if(!pending || pending.text !== text || pending.room !== room || pending.file !== file) {
    let file_id
    if(file) { const form = new FormData(); form.append('file',file); file_id=(await api(`/rooms/${room}/files`,'POST',form)).id }
    pending={room,text,client_id:crypto.randomUUID(),file_id,file}
  }
  const {file:unused,...payload}=pending
  socket.timeout(8000).emit('message',payload,(error,result) => {
    if(error || !result?.ok) return status(result?.error || 'Acknowledgment delayed. Send again to retry safely.')
    if(room === payload.room) render(result.message)
    pending=null; $('message').value=''; $('file').value=''
  })
})
api('/session').then(async data => { csrf=data.csrf; user=data.username; if(user) await enter() }).catch(e => status(e.message))
