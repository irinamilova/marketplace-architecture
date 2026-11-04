'use strict';
const $ = id => document.getElementById(id);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (minor, currency = 'RUB') => new Intl.NumberFormat('ru-RU', {style:'currency', currency, maximumFractionDigits:2}).format(minor / 100);
const time = value => new Date(value).toLocaleTimeString('ru-RU', {hour:'2-digit',minute:'2-digit',timeZone:'UTC'});
const day = value => new Date(value).toLocaleDateString('ru-RU', {day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
const airports = {SVO:'Шереметьево',VKO:'Внуково',LED:'Пулково'};
const statuses = {CONFIRMED:'Подтверждено',CANCELLED:'Отменено',SCHEDULED:'По расписанию',DEPARTED:'Вылетел',COMPLETED:'Завершён'};
let selectedFlight = null, cancelId = null, reservePending = false, cancelPending = false;
let searchVersion = 0, bookingsVersion = 0;
function feedback(id, text = '', kind = '') { $(id).textContent = text; $(id).className = `feedback ${kind}`; }
function empty(title, text) { return `<div class="empty"><strong>${escapeHtml(title)}</strong>${escapeHtml(text)}</div>`; }
async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, {...options, headers:{'Content-Type':'application/json'}, signal:AbortSignal.timeout(25000)}); }
  catch { throw new Error('Не удалось получить ответ сервера. Проверьте соединение и список бронирований перед повторной отправкой.'); }
  const data = await response.json();
  if (!response.ok) {
    const labels = {400:'Проверьте данные запроса.',404:'Рейс или бронирование не найдены.',409:'Операция недоступна: проверьте статус бронирования и количество свободных мест.',422:'Проверьте заполнение полей.',502:'Сервис рейсов вернул ошибку.',503:'Сервис временно недоступен. Попробуйте позже.',504:'Сервис не ответил вовремя.'};
    const details = Array.isArray(data.detail) ? data.detail.map(e => `${e.loc.at(-1)}: ${e.msg}`).join('; ') : data.detail;
    throw new Error(`${labels[response.status] || 'Не удалось выполнить запрос'} HTTP ${response.status}${details ? ` · ${details}` : ''}${options.method === 'POST' && response.status >= 500 ? ' Проверьте список бронирований перед повторной отправкой: восстановление может ещё выполняться.' : ''}`);
  }
  return data;
}
function showPage(page) {
  for (const name of ['search','bookings']) {
    $(`${name}-page`).hidden = page !== name;
    $(`nav-${name}`).classList.toggle('active', page === name);
    if (page === name) $(`nav-${name}`).setAttribute('aria-current','page'); else $(`nav-${name}`).removeAttribute('aria-current');
  }
  $('breadcrumb').textContent = page === 'search' ? 'Поиск рейсов' : 'Бронирования';
}
$('nav-search').onclick = () => showPage('search');
$('nav-bookings').onclick = () => { showPage('bookings'); loadBookings(); };
$('swap').onclick = () => { const origin = $('origin').value; $('origin').value = $('destination').value; $('destination').value = origin; };
for (const id of ['origin','destination']) $(id).addEventListener('input', () => { $(id).value = $(id).value.toUpperCase(); });
function flightCard(f) {
  const minutes = Math.round((new Date(f.arrival_time) - new Date(f.departure_time))/60000);
  return `<article class="flight-card"><div class="flight-top"><span class="airline-icon">${escapeHtml(f.airline_code)}</span><strong>${escapeHtml(f.airline_name)}</strong><span class="flight-number">${escapeHtml(f.flight_number)}</span><span class="badge">${escapeHtml(statuses[f.status] || f.status)}</span></div><div class="flight-main"><div><div class="time">${time(f.departure_time)}</div><div class="airport">${escapeHtml(f.origin)} · ${escapeHtml(airports[f.origin] || f.origin)}</div><div class="date-label">${day(f.departure_time)}</div></div><div class="route-line">${Math.floor(minutes/60)} ч ${minutes%60} мин<div class="route-track"></div>Без пересадок</div><div><div class="time">${time(f.arrival_time)}</div><div class="airport">${escapeHtml(f.destination)} · ${escapeHtml(airports[f.destination] || f.destination)}</div><div class="date-label">${day(f.arrival_time)}</div></div><div class="price-block"><div class="price">${money(f.price_minor,f.currency)}</div><small>за место · свободно ${f.available_seats}</small><button class="primary" data-flight="${escapeHtml(f.id)}" ${f.available_seats < 1 ? 'disabled' : ''}>${f.available_seats < 1 ? 'Нет мест' : 'Выбрать рейс'}</button></div></div></article>`;
}
async function searchFlights() {
  const version = ++searchVersion;
  const button = $('search-form').querySelector('[type=submit]'); button.disabled = true;
  feedback('search-feedback','Ищем доступные рейсы…'); $('flights').replaceChildren(); $('flight-count').textContent = '';
  const params = new URLSearchParams({origin:$('origin').value.toUpperCase(),destination:$('destination').value.toUpperCase()});
  if ($('date').value) params.set('date',$('date').value);
  try {
    const flights = await api(`/flights?${params}`); if (version !== searchVersion) return;
    feedback('search-feedback'); $('flight-count').textContent = `Найдено: ${flights.length}`;
    $('flights').innerHTML = flights.length ? flights.map(flightCard).join('') : empty('Рейсов не найдено','Попробуйте другую дату или направление. Можно оставить дату пустой.');
  } catch (error) { if (version === searchVersion) feedback('search-feedback',error.message,'error'); }
  finally { if (version === searchVersion) button.disabled = false; }
}
$('search-form').onsubmit = event => { event.preventDefault(); searchFlights(); };
$('demo-route').onclick = () => { $('origin').value='SVO'; $('destination').value='LED'; $('date').value='2026-11-20'; searchFlights(); };
$('flights').onclick = async event => {
  const button = event.target.closest('[data-flight]'); if (!button) return;
  button.disabled = true;
  try {
    const f = await api(`/flights/${encodeURIComponent(button.dataset.flight)}`); selectedFlight = f;
    $('flight-title').textContent = `${f.origin} → ${f.destination}`;
    $('flight-detail').innerHTML = `<div class="detail-summary"><strong>${escapeHtml(f.airline_name)} · ${escapeHtml(f.flight_number)}</strong>${day(f.departure_time)} · ${time(f.departure_time)} → ${time(f.arrival_time)} UTC<br>Прилёт: ${day(f.arrival_time)} · ${escapeHtml(statuses[f.status])}<br>Свободно ${f.available_seats} из ${f.total_seats} мест</div>`;
    $('reserve-user').value = $('user-id').value; $('seats').value = '1'; $('seats').max = Math.max(1,f.available_seats);
    $('reserve-submit').disabled = f.available_seats < 1 || f.status !== 'SCHEDULED';
    feedback('reserve-feedback', $('reserve-submit').disabled ? 'Этот рейс сейчас недоступен для бронирования.' : '');
    updateTotal(); $('flight-dialog').showModal();
  } catch (error) { feedback('search-feedback',error.message,'error'); }
  finally { button.disabled = false; }
};
function updateTotal() { if (selectedFlight) $('total').textContent = money(selectedFlight.price_minor * Number($('seats').value || 0),selectedFlight.currency); }
$('seats').oninput = updateTotal;
$('reserve-form').onsubmit = async event => {
  event.preventDefault(); if (reservePending || !selectedFlight) return;
  for (const id of ['reserve-user','passenger','email']) $(id).value = $(id).value.trim();
  if (!$('reserve-form').reportValidity()) return;
  reservePending = true; $('reserve-submit').disabled = true; feedback('reserve-feedback','Бронируем места…');
  try {
    const booking = await api('/bookings',{method:'POST',body:JSON.stringify({user_id:$('reserve-user').value,flight_id:selectedFlight.id,passenger_name:$('passenger').value,passenger_email:$('email').value,seat_count:Number($('seats').value)})});
    $('user-id').value = booking.user_id; $('flight-dialog').close(); $('passenger').value=''; $('email').value='';
    showPage('bookings'); const loaded = await loadBookings(); if (loaded) feedback('bookings-feedback',`Бронирование подтверждено. Номер: ${booking.id}`,'success');
    searchFlights();
  } catch (error) { feedback('reserve-feedback',error.message,'error'); }
  finally { reservePending = false; $('reserve-submit').disabled = false; }
};
function bookingCard(b) {
  return `<article class="booking-card"><div class="booking-head"><h3>${escapeHtml(b.passenger_name)}</h3><span class="badge ${b.status === 'CANCELLED' ? 'cancelled' : ''}">${escapeHtml(statuses[b.status])}</span></div><div class="booking-meta"><div><small>Номер бронирования</small><span class="uuid">${escapeHtml(b.id)}</span></div><div><small>Рейс</small><span data-route="${escapeHtml(b.flight_id)}" class="uuid">${escapeHtml(b.flight_id)}</span></div><div><small>Контакт пассажира</small>${escapeHtml(b.passenger_email)}</div><div><small>Места · Дата бронирования (UTC)</small>${b.seat_count} · ${day(b.created_at)}</div></div><div class="booking-actions"><strong>${money(b.total_price_minor,b.currency)}</strong><button class="secondary" data-booking="${escapeHtml(b.id)}">Обновить детали</button>${b.status === 'CONFIRMED' ? `<button class="secondary" data-cancel="${escapeHtml(b.id)}">Отменить</button>` : ''}</div></article>`;
}
async function loadBookings(id) {
  const version = ++bookingsVersion; feedback('bookings-feedback','Загружаем бронирования…'); $('bookings').replaceChildren(); $('booking-count').textContent='';
  try {
    const data = await api(id ? `/bookings/${encodeURIComponent(id)}` : `/bookings?${new URLSearchParams({user_id:$('user-id').value.trim()})}`);
    if (version !== bookingsVersion) return;
    const list = id ? [data] : data; feedback('bookings-feedback'); $('booking-count').textContent=`Всего: ${list.length}`;
    $('bookings').innerHTML = list.length ? list.map(bookingCard).join('') : empty('Бронирований пока нет','Выберите рейс и укажите этот ID пользователя при оформлении.');
    // Booking history remains available even when Flight is unavailable.
    for (const fid of new Set(list.map(b => b.flight_id))) {
      api(`/flights/${encodeURIComponent(fid)}`).then(f => {
        if (version !== bookingsVersion) return;
        for (const node of $('bookings').querySelectorAll('[data-route]')) {
          if (node.dataset.route === fid) {
            node.textContent = `${f.origin} → ${f.destination} · ${f.flight_number} · ${day(f.departure_time)}, ${time(f.departure_time)} UTC`;
            node.className = '';
          }
        }
      }).catch(() => {});
    }
    return true;
  } catch (error) { if (version === bookingsVersion) feedback('bookings-feedback',error.message,'error'); return false; }
}
$('bookings-form').onsubmit = event => { event.preventDefault(); $('user-id').value=$('user-id').value.trim(); if ($('bookings-form').reportValidity()) loadBookings(); };
$('booking-id-form').onsubmit = event => { event.preventDefault(); const id=$('booking-id').value.trim(); if (id) loadBookings(id); };
$('bookings').onclick = event => {
  const details = event.target.closest('[data-booking]'); if (details) { loadBookings(details.dataset.booking); return; }
  const cancel = event.target.closest('[data-cancel]'); if (!cancel) return;
  cancelId=cancel.dataset.cancel; feedback('cancel-feedback'); $('cancel-dialog').showModal();
};
$('confirm-cancel').onclick = async () => {
  if (cancelPending || !cancelId) return;
  cancelPending = true; $('confirm-cancel').disabled=true; feedback('cancel-feedback','Отменяем бронирование…');
  try {
    const booking = await api(`/bookings/${encodeURIComponent(cancelId)}/cancel`,{method:'POST'});
    $('cancel-dialog').close(); $('user-id').value=booking.user_id; const loaded = await loadBookings();
    if (loaded) feedback('bookings-feedback','Бронирование отменено. Места возвращены в продажу.','success'); searchFlights();
  } catch (error) { feedback('cancel-feedback',error.message,'error'); }
  finally { cancelPending=false; $('confirm-cancel').disabled=false; }
};
for (const button of document.querySelectorAll('[data-close]')) button.onclick = () => { if (!reservePending && !cancelPending) $(button.dataset.close).close(); };
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('cancel', event => { if (reservePending || cancelPending) event.preventDefault(); });
searchFlights();
