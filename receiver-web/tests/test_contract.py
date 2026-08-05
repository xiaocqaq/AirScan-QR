from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_receiver_page_is_file_protocol_compatible():
    html = (ROOT / "receiver.html").read_text(encoding="utf-8")
    assert '<script defer src="app.js"></script>' in html
    assert 'type="module"' not in html
    for name in (
        "protocol.js", "sha1.js", "decoder.js", "capture.js",
        "transfer-store.js", "receiver-core.js", "messages.js", "app.js",
    ):
        assert f'src="{name}"' in html


def test_receiver_ui_connects_storage_and_downloads():
    html = (ROOT / "receiver.html").read_text(encoding="utf-8")
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    assert 'id="messageList"' in html
    assert 'id="messageEmpty"' in html
    assert 'id="messageCount"' in html
    assert "openStore" in app
    assert "ReceiverCore" in app
    assert "formatMissing" in app
    assert "URL.createObjectURL" in app
    assert "result.flags & global.AirScan.protocol.FLAG_TEXT" in app
    assert "addTextMessage" in app
    expected = "shareButton.disabled = false;\n      shareButton.textContent = '更换共享窗口';"
    assert expected in app
    assert "const isNewTask = !sameTask(state.task, task);" in app
    assert "if (isNewTask) clearCompletedDownload();" in app
    assert "function onCaptureError(error) {\n    captureController.pause();" in app


def test_text_completion_never_uses_file_download_path():
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    start = app.index("if (result.flags & global.AirScan.protocol.FLAG_TEXT)")
    end = app.index("state.completed = result;", start)
    text_branch = app[start:end]
    assert "return;" in text_branch
    assert "downloadCurrent" not in text_branch


def test_receiver_layout_prioritizes_messages_and_thumbnail():
    html = (ROOT / "receiver.html").read_text(encoding="utf-8")
    css = (ROOT / "styles.css").read_text(encoding="utf-8")
    order = [html.index(token) for token in (
        'class="message-panel"', 'class="control-panel"',
        'class="notice"', 'class="stage-panel"',
    )]
    assert order == sorted(order)
    assert "aspect-ratio: 16 / 9" in css
    assert "max-height: 220px" in css
    assert ".message-list" in css


def test_decoder_has_network_fallback_and_grid_scanning():
    decoder = (ROOT / "decoder.js").read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js" in decoder
    assert "vendor/jsQR.js" in decoder
    assert "Promise.all" in decoder
    assert "grid === 1" in decoder
    assert "LOAD_TIMEOUT_MS" in decoder
    assert "clearTimeout" in decoder


def test_capture_and_store_use_browser_native_apis():
    capture = (ROOT / "capture.js").read_text(encoding="utf-8")
    store = (ROOT / "transfer-store.js").read_text(encoding="utf-8")
    assert "getDisplayMedia" in capture
    assert "requestAnimationFrame" in capture
    assert "indexedDB.open" in store
    assert "keyPath: ['tid', 'index']" in store


def test_readme_documents_browser_security_limits():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "双击" in readme
    assert "默认下载目录" in readme
    assert "Chrome" in readme
