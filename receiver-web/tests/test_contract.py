from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_receiver_page_is_file_protocol_compatible():
    html = (ROOT / "receiver.html").read_text(encoding="utf-8")
    assert '<script defer src="app.js"></script>' in html
    assert 'type="module"' not in html
    for name in (
        "protocol.js", "sha1.js", "decoder.js", "capture.js",
        "transfer-store.js", "receiver-core.js", "app.js",
    ):
        assert f'src="{name}"' in html


def test_receiver_ui_connects_storage_and_downloads():
    app = (ROOT / "app.js").read_text(encoding="utf-8")
    assert "openStore" in app
    assert "ReceiverCore" in app
    assert "formatMissing" in app
    assert "URL.createObjectURL" in app
    expected = "shareButton.disabled = false;\n      shareButton.textContent = '更换共享窗口';"
    assert expected in app
    assert "const isNewTask = !sameTask(state.task, task);" in app
    assert "if (isNewTask) clearCompletedDownload();" in app
    assert "function onCaptureError(error) {\n    captureController.pause();" in app


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
