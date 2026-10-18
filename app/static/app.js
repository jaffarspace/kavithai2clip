(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const sample = {
    title: 'மழைக்குப் பிறகு',
    poem: 'நிலவின் மௌனத்தில் உன் நினைவு மலர்கிறது.\nமழைத்துளி ஒவ்வொன்றும் சொல்லாத கதைகள் பேசுகிறது.\nகடந்த காலம் கடலலையாய் கரையைத் தொடுகிறது.\nவிடியலின் ஒளியில் என் கனவு மீண்டும் பிறக்கிறது.'
  };
  const fallbackThemes = [
    {id: 'moon', name: 'Moonlight', description: 'Moonlight & memory'},
    {id: 'rain', name: 'Rain', description: 'Rain & quiet reflection'},
    {id: 'ocean', name: 'Ocean', description: 'The rhythm of the sea'},
    {id: 'forest', name: 'Forest', description: 'A quiet, living world'},
    {id: 'sunrise', name: 'Sunrise', description: 'A new beginning'},
    {id: 'city', name: 'City', description: 'Stories in the everyday'},
    {id: 'love', name: 'Warmth', description: 'A little warmth & tenderness'},
    {id: 'abstract', name: 'Abstract', description: 'A feeling without boundaries'}
  ];
  const state = {scenes: [], activeScene: 0, style: 'cinematic', themes: fallbackThemes, generating: false, rendering: false, uploading: 0, narrationAsset: null, voiceTouched: false, source: '', pollTimer: null, pollStarted: 0, capabilities: {}};
  let dirtyNoticeTimer;

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 90000);
    try {
      const response = await fetch(url, {...options, signal: controller.signal});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('The studio returned an unexpected response. Please try again.'); }
      if (!response.ok) {
        let detail = data.detail || data.error || 'Something went wrong. Please try again.';
        if (Array.isArray(detail)) detail = detail.map(item => `${(item.loc || []).filter(p => p !== 'body').join(' → ')}: ${item.msg}`).join('; ');
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('That request took longer than expected. Please try again.');
      if (error instanceof TypeError) throw new Error('The studio could not connect. Check your connection and try again.');
      throw error;
    } finally { window.clearTimeout(timeout); }
  }

  function showError(message) {
    $('global-error').textContent = message;
    $('global-error').hidden = !message;
    if (message) $('global-error').scrollIntoView({behavior: 'smooth', block: 'center'});
  }
  function syncButtons() {
    $('storyboard-button').disabled = state.generating || state.rendering || state.uploading > 0;
    $('storyboard-button').querySelector('span').textContent = state.generating ? 'Finding the scenes…' : 'Create my storyboard';
    $('render-button').disabled = state.generating || state.rendering || state.uploading > 0 || !state.scenes.length;
    $('render-button').querySelector('span:last-child').textContent = state.rendering ? 'Making your film…' : state.uploading > 0 ? 'Uploading…' : 'Make my film';
    document.body.classList.toggle('is-busy', state.generating || state.rendering);
  }
  function countCharacters() { $('character-count').textContent = `${Array.from($('poem').value).length.toLocaleString()} / 4,000`; }
  function themeFor(id) { return state.themes.find(theme => theme.id === id) || fallbackThemes.find(theme => theme.id === id) || fallbackThemes[0]; }
  function totalDuration() {
    const total = state.scenes.reduce((sum, scene) => sum + (Number(scene.duration) || 0), 0);
    $('total-duration').textContent = `${Number.isInteger(total) ? total : total.toFixed(1)} seconds`;
    $('render-estimate').textContent = `${state.scenes.length} scenes · ${Number.isInteger(total) ? total : total.toFixed(1)} seconds · one little film`;
  }
  function setStep(number) {
    ['write', 'scenes', 'export'].forEach((name, index) => {
      const element = $(`step-${name}`);
      element.classList.toggle('is-current', index === number - 1);
      element.classList.toggle('is-complete', index < number - 1);
    });
  }
  function updatePreview() {
    const scene = state.scenes[state.activeScene];
    const themeId = scene ? scene.theme : 'moon';
    const theme = themeFor(themeId);
    const preview = $('portrait-preview');
    preview.className = `portrait-preview theme-${fallbackThemes.some(t => t.id === themeId) ? themeId : 'abstract'} style-${state.style}`;
    const art = preview.querySelector('.scene-art');
    art.style.backgroundImage = '';
    if (scene?.image_url) {
      preview.classList.add('has-image');
      art.style.backgroundImage = `url("${scene.image_url.replace(/["\\\n\r]/g, '')}")`;
    }
    $('preview-text').textContent = scene ? scene.text : ($('poem').value.split('\n').filter(Boolean)[0] || 'உங்கள் கவிதையின் உலகம்…');
    $('preview-title').textContent = $('title').value;
    $('preview-author').textContent = $('author').value ? `— ${$('author').value}` : '';
    const number = String(state.activeScene + 1).padStart(2, '0');
    $('preview-scene-number').textContent = number;
    $('preview-counter').textContent = scene ? `SCENE ${number} / ${String(state.scenes.length).padStart(2, '0')}` : 'SCENE 01';
    $('preview-scene-theme').textContent = theme.description || theme.name;
    $('preview-caption').textContent = scene?.image_id ? 'Your image, with the scene’s words on screen.' : 'Storyboard preview · your export adds motion & sound.';
    $('previous-scene').disabled = !state.scenes.length || state.activeScene === 0;
    $('next-scene').disabled = !state.scenes.length || state.activeScene === state.scenes.length - 1;
    document.querySelectorAll('.scene-card').forEach((card, index) => card.classList.toggle('is-selected', index === state.activeScene));
  }
  function selectScene(index) { state.activeScene = Math.min(Math.max(index, 0), Math.max(state.scenes.length - 1, 0)); updatePreview(); }
  function markChanged() {
    countCharacters();
    updatePreview();
    window.clearTimeout(dirtyNoticeTimer);
    if (state.scenes.length) dirtyNoticeTimer = window.setTimeout(() => { $('change-notice').hidden = sourceKey() === state.source; }, 200);
  }
  function sourceKey() { return JSON.stringify({poem: $('poem').value.trim(), style: state.style, mood: $('mood').value}); }
  function createElement(tag, className, text) { const element = document.createElement(tag); if (className) element.className = className; if (text !== undefined) element.textContent = text; return element; }

  function buildScenes() {
    $('scene-list').replaceChildren();
    state.scenes.forEach((scene, index) => {
      const card = createElement('article', 'scene-card');
      card.dataset.scene = String(index);
      const top = createElement('div', 'scene-card-top');
      top.append(createElement('span', 'scene-label', `SCENE ${String(index + 1).padStart(2, '0')}`));
      const previewButton = createElement('button', 'preview-scene-button', 'Preview scene');
      const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      arrow.setAttribute('class', 'arrow-icon'); arrow.setAttribute('viewBox', '0 0 20 20'); arrow.setAttribute('aria-hidden', 'true');
      const arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path'); arrowPath.setAttribute('d', 'M5 15 15 5M5 5h10v10'); arrow.append(arrowPath); previewButton.append(arrow);
      previewButton.type = 'button';
      previewButton.addEventListener('click', () => { selectScene(index); if (window.innerWidth < 671) $('portrait-preview').scrollIntoView({behavior:'smooth', block:'center'}); });
      top.append(previewButton);
      card.append(top);
      const text = createElement('textarea', 'scene-text');
      text.lang = 'ta'; text.value = scene.text; text.rows = 2; text.maxLength = 400;
      text.setAttribute('aria-label', `Scene ${index + 1} subtitle`);
      text.addEventListener('input', () => { scene.text = text.value; selectScene(index); });
      card.append(text);
      const fields = createElement('div', 'scene-fields');
      const themeField = createElement('div', 'scene-field');
      const themeLabel = createElement('label', '', 'The atmosphere');
      themeLabel.htmlFor = `theme-${index}`;
      const themeSelect = document.createElement('select'); themeSelect.id = `theme-${index}`;
      state.themes.forEach(theme => { const option = document.createElement('option'); option.value = theme.id; option.textContent = theme.name; themeSelect.append(option); });
      themeSelect.value = scene.theme;
      if (!themeSelect.value) { themeSelect.value = 'abstract'; scene.theme = 'abstract'; }
      themeSelect.addEventListener('change', () => { scene.theme = themeSelect.value; scene.visual = themeFor(scene.theme).description || ''; visual.textContent = scene.visual; selectScene(index); });
      themeField.append(themeLabel, themeSelect);
      const durationField = createElement('div', 'scene-field');
      const durationLabel = createElement('label', '', 'Time to linger'); durationLabel.htmlFor = `duration-${index}`;
      const durationWrap = createElement('div', 'duration-input');
      const duration = document.createElement('input'); duration.id = `duration-${index}`; duration.type = 'number'; duration.min = '3'; duration.max = '10'; duration.step = '0.5'; duration.value = scene.duration;
      duration.addEventListener('input', () => { scene.duration = Number(duration.value); totalDuration(); });
      duration.addEventListener('change', () => { if (!duration.checkValidity()) { duration.value = Math.min(10, Math.max(3, Number(duration.value) || 5)); scene.duration = Number(duration.value); totalDuration(); } });
      durationWrap.append(duration, createElement('span', '', 'sec'));
      durationField.append(durationLabel, durationWrap); fields.append(themeField, durationField); card.append(fields);
      const visual = createElement('p', 'scene-visual', scene.visual || themeFor(scene.theme).description || ''); card.append(visual);
      const uploadRow = createElement('div', 'scene-upload-row');
      const upload = createElement('label', 'upload-button', '↑  Add your own photo'); upload.htmlFor = `image-${index}`;
      const imageInput = createElement('input', 'file-input'); imageInput.type = 'file'; imageInput.accept = 'image/jpeg,image/png,image/webp'; imageInput.id = `image-${index}`;
      const imageName = createElement('span', 'scene-image-name', scene.image_name || '');
      const remove = createElement('button', 'remove-image', '×'); remove.type = 'button'; remove.hidden = !scene.image_id; remove.setAttribute('aria-label', `Remove image for scene ${index + 1}`);
      remove.addEventListener('click', () => { delete scene.image_id; delete scene.image_url; delete scene.image_name; imageName.textContent = ''; imageInput.value = ''; remove.hidden = true; selectScene(index); });
      imageInput.addEventListener('change', async () => {
        const file = imageInput.files[0]; if (!file) return;
        try {
          const asset = await uploadAsset(file, 'image');
          scene.image_id = asset.id; scene.image_url = asset.url; scene.image_name = asset.name || file.name;
          imageName.textContent = scene.image_name; remove.hidden = false; selectScene(index);
        } catch (error) { showError(error.message); imageInput.value = ''; }
      });
      uploadRow.append(upload, imageInput, imageName, remove); card.append(uploadRow); $('scene-list').append(card);
    });
    $('scene-count').textContent = `${state.scenes.length} ${state.scenes.length === 1 ? 'scene' : 'scenes'}`;
    totalDuration(); updatePreview();
  }

  async function uploadAsset(file, expectedKind) {
    if (state.rendering) throw new Error('Please wait for the current film to finish before adding a new file.');
    if (file.size > 25 * 1024 * 1024) throw new Error('Please choose a file smaller than 25 MB.');
    state.uploading++; syncButtons(); showError('');
    const body = new FormData(); body.append('file', file);
    try {
      const asset = await request('/api/assets', {method: 'POST', body});
      if (asset.kind !== expectedKind) throw new Error(`Please choose ${expectedKind === 'image' ? 'an image' : 'an audio'} file.`);
      return asset;
    } finally { state.uploading--; syncButtons(); }
  }

  $('poem-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (state.generating || state.rendering) return;
    const poem = $('poem').value.trim();
    if (!poem) { showError('Add a few lines of poetry to begin your film.'); $('poem').focus(); return; }
    state.generating = true; syncButtons(); showError('');
    try {
      const source = sourceKey();
      const result = await request('/api/storyboard', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({poem, title: $('title').value.trim(), author: $('author').value.trim(), style: state.style, mood: $('mood').value})});
      if (!Array.isArray(result.scenes) || !result.scenes.length) throw new Error('No scenes were created. Try separating your poem into a few lines.');
      state.scenes = result.scenes.map(scene => ({...scene, duration: Number(scene.duration) || 5}));
      state.activeScene = 0; state.source = source;
      if (!$('title').value && result.title) { $('title').value = result.title; state.source = sourceKey(); }
      $('interpretation').textContent = result.interpretation || 'Review the scene suggestions. Change the words, atmosphere, or timing until the film feels like you.';
      $('storyboard-section').hidden = false; $('export-section').hidden = false; $('change-notice').hidden = true;
      buildScenes(); setStep(2);
      $('storyboard-section').scrollIntoView({behavior: 'smooth', block: 'start'});
    } catch (error) { showError(error.message); }
    finally { state.generating = false; syncButtons(); }
  });
  $('sample-button').addEventListener('click', () => {
    $('poem').value = sample.poem; $('title').value = sample.title; markChanged(); $('poem').focus();
  });
  ['poem', 'title', 'author'].forEach(id => $(id).addEventListener('input', markChanged));
  $('mood').addEventListener('change', markChanged);
  document.querySelectorAll('[data-style]').forEach(button => button.addEventListener('click', () => {
    state.style = button.dataset.style;
    document.querySelectorAll('[data-style]').forEach(choice => { const selected = choice === button; choice.classList.toggle('selected', selected); choice.setAttribute('aria-pressed', selected); });
    markChanged();
  }));
  $('previous-scene').addEventListener('click', () => selectScene(state.activeScene - 1));
  $('next-scene').addEventListener('click', () => selectScene(state.activeScene + 1));
  $('narration').addEventListener('change', (event) => {
    if (event.isTrusted) state.voiceTouched = true;
    const value = $('narration').value;
    $('narration-upload').hidden = value !== 'upload';
    $('voice-note').textContent = ['male', 'female'].includes(value) ? 'Online voice: your poem text is sent to Microsoft’s speech service. A network connection is required.' : value === 'upload' ? 'Use recordings you have permission to use. Scenes will stretch to fit a longer reading.' : state.capabilities.online_voice_available === false ? 'Online voices are unavailable here. Upload your own reading, or enjoy captions and music.' : 'Let the poetry speak through subtitles, or add a reading.';
  });
  $('audio-file').addEventListener('change', async () => {
    const file = $('audio-file').files[0]; if (!file) return;
    state.narrationAsset = null;
    $('audio-name').textContent = 'Uploading your reading…';
    try { const asset = await uploadAsset(file, 'audio'); state.narrationAsset = asset.id; $('audio-name').textContent = asset.name || file.name; }
    catch (error) { showError(error.message); $('audio-name').textContent = 'Choose another audio file'; $('audio-file').value = ''; }
  });
  function setProgress(progress, stage) {
    const percent = Math.min(100, Math.max(0, Math.round(Number(progress) || 0)));
    $('progress-bar').style.width = `${percent}%`; $('progress-percent').textContent = `${percent}%`;
    $('progress-stage').textContent = stage || 'Making your film…';
  }
  function finishRender() {
    state.rendering = false; syncButtons(); window.clearTimeout(state.pollTimer);
  }
  async function pollJob(jobId, failureCount = 0) {
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
      setProgress(job.progress, job.stage);
      if (job.status === 'complete') {
        if (!job.video_url) throw new Error('The film finished, but its download link is missing. Please try again.');
        $('result-video').src = job.video_url;
        if (job.poster_url) $('result-video').poster = job.poster_url;
        else $('result-video').removeAttribute('poster');
        $('download-link').href = job.video_url;
        $('result-section').hidden = false; setProgress(100, 'Your film is ready.'); finishRender(); setStep(3);
        $('result-section').scrollIntoView({behavior:'smooth', block:'center'}); return;
      }
      if (job.status === 'failed') throw new Error(job.error || 'The film could not be rendered. Please try again.');
      if (Date.now() - state.pollStarted > 20 * 60 * 1000) throw new Error('Your film is taking longer than expected. Please try a shorter poem or draft quality.');
      state.pollTimer = window.setTimeout(() => pollJob(jobId), 1600);
    } catch (error) {
      if (failureCount < 2 && /connect|request took|unexpected response/i.test(error.message)) { state.pollTimer = window.setTimeout(() => pollJob(jobId, failureCount + 1), 3000); return; }
      finishRender(); $('progress-stage').textContent = 'The film needs another take.'; showError(error.message);
    }
  }
  $('render-button').addEventListener('click', async () => {
    if (state.rendering || !state.scenes.length || state.uploading) return;
    if (state.scenes.some(scene => !scene.text.trim())) { showError('Each scene needs a few words. Add a subtitle to the empty scene before making your film.'); return; }
    if (state.scenes.some(scene => !Number.isFinite(Number(scene.duration)) || Number(scene.duration) < 3 || Number(scene.duration) > 10)) { showError('Give each scene between 3 and 10 seconds.'); return; }
    if ($('narration').value === 'upload' && !state.narrationAsset) { showError('Choose your voice recording before making the film.'); $('audio-file').focus(); return; }
    state.rendering = true; syncButtons(); showError(''); setStep(3);
    $('render-progress').hidden = false; $('result-section').hidden = true; setProgress(0, 'Preparing your film…');
    const payload = {title: $('title').value.trim(), author: $('author').value.trim(), style: state.style, mood: $('mood').value, scenes: state.scenes.map(scene => ({text: scene.text.trim(), theme: scene.theme, duration: Number(scene.duration), ...(scene.image_id ? {image_id: scene.image_id} : {})})), music: $('music').checked, narration: $('narration').value, narration_asset_id: $('narration').value === 'upload' ? state.narrationAsset : null, quality: $('quality').value};
    try {
      const result = await request('/api/render', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if (!result.job_id) throw new Error('The film could not be started. Please try again.');
      state.pollStarted = Date.now(); pollJob(result.job_id);
    } catch (error) { finishRender(); $('progress-stage').textContent = 'The film needs another take.'; showError(error.message); }
  });
  async function loadCapabilities() {
    try {
      state.capabilities = await request('/api/capabilities');
      if (Array.isArray(state.capabilities.themes) && state.capabilities.themes.length) state.themes = state.capabilities.themes;
      if (state.capabilities.online_voice_available === false) {
        ['female', 'male'].forEach(value => { const option = $('narration').querySelector(`option[value="${value}"]`); option.disabled = true; option.textContent += ' · unavailable'; });
        $('voice-note').textContent = 'Online voices are unavailable here. Upload your own reading, or enjoy captions and music.';
      } else if (state.capabilities.online_voice_available === true && !state.voiceTouched) { $('narration').value = 'male'; $('narration').dispatchEvent(new Event('change')); }
      updatePreview();
    } catch (_) { /* The editable studio remains useful while the service starts. */ }
  }
  countCharacters(); updatePreview(); loadCapabilities();
})();
