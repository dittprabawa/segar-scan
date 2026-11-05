const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

test('script kamera dan app dimuat berurutan, capture hanya satu kali', async () => {
    const elements = new Map();
    const element = id => {
        if (!elements.has(id)) elements.set(id, {
            handlers: {}, videoWidth: 1280, videoHeight: 720,
            addEventListener(event, fn) {
                (this.handlers[event] ??= []).push(fn);
            },
        });
        return elements.get(id);
    };
    const windowEvents = {};
    const context = vm.createContext({
        document: {getElementById: element},
        window: {addEventListener: (name, fn) => {windowEvents[name] = fn;}},
        console: {log() {}, error() {}},
    });
    for (const file of ['camera.js', 'app.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../../frontend/js', file), 'utf8'), context);
    }
    assert.equal(element('btn-capture').handlers.click.length, 1);
    assert.equal(typeof windowEvents.pagehide, 'function');
    vm.runInContext(`
        var sent = 0;
        captureCenterCrop = async () => ({type: 'image/jpeg'});
        showPreview = () => {};
        sendImage = async () => {sent += 1; busy = false;};
    `, context);
    await element('btn-capture').handlers.click[0]();
    assert.equal(context.sent, 1);
    element('video').videoWidth = 0;
    await element('btn-capture').handlers.click[0]();
    assert.equal(context.sent, 1);
    assert.match(element('camera-message').textContent, /belum siap/);
});
