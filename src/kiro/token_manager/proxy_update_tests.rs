use super::*;
use crate::admin::AdminService;
use axum::http::StatusCode;
use std::path::PathBuf;
use std::sync::mpsc;

struct Fixture {
    root: PathBuf,
    manager: Arc<MultiTokenManager>,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("kiro-proxy-update-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        let config = Config::load(root.join("config.json")).unwrap();
        let mut config = config;
        config.proxy_url = Some("http://127.0.0.1:31001".into());
        config.proxy_username = Some("offline-user".into());
        config.proxy_password = Some("offline-old".into());
        config.save().unwrap();
        let proxy = Some(
            ProxyConfig::new("http://127.0.0.1:31001").with_auth("offline-user", "offline-old"),
        );
        let manager = Arc::new(
            MultiTokenManager::new(config, vec![KiroCredentials::default()], proxy, None, false)
                .unwrap(),
        );
        Self { root, manager }
    }

    fn path(&self) -> PathBuf {
        self.root.join("config.json")
    }

    fn assert_persisted(&self, expected: Option<ProxyConfig>) {
        assert_eq!(self.manager.proxy(), expected);
        let config = Config::load(self.path()).unwrap();
        assert_eq!(config.proxy_url, expected.as_ref().map(|p| p.url.clone()));
        assert_eq!(
            config.proxy_username,
            expected.as_ref().and_then(|p| p.username.clone())
        );
        assert_eq!(config.proxy_password, expected.and_then(|p| p.password));
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

#[tokio::test]
async fn http_proxy_password_patch_distinguishes_omitted_null_empty_and_value() {
    use crate::admin::{
        AdminState, ClientKeyManager, GroupManager, TraceStore, UsageAggregator,
        create_admin_router,
    };
    let fixture = Fixture::new();
    let state = AdminState::new(
        "offline-admin",
        AdminService::new(fixture.manager.clone(), Vec::new()),
        Arc::new(ClientKeyManager::new()),
        Arc::new(UsageAggregator::new()),
        Arc::new(TraceStore::open_in_memory().unwrap()),
        Arc::new(GroupManager::new()),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        axum::serve(listener, create_admin_router(state))
            .await
            .unwrap()
    });
    // Abort even if an assertion fails, keeping this isolated test server local.
    struct ServerGuard(tokio::task::JoinHandle<()>);
    impl Drop for ServerGuard {
        fn drop(&mut self) {
            self.0.abort();
        }
    }
    let _server = ServerGuard(server);
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    for (patch, expected) in [
        (None, Some("offline-old")),
        (Some(serde_json::json!("offline-new")), Some("offline-new")),
        (Some(serde_json::Value::Null), None),
        (Some(serde_json::json!("")), None),
    ] {
        fixture
            .manager
            .update_global_proxy(
                Some("http://127.0.0.1:31001".into()),
                Some("offline-user".into()),
                Some(Some("offline-old".into())),
            )
            .unwrap();
        let mut body = serde_json::json!({"proxyUrl":"http://127.0.0.1:31002", "proxyUsername":"offline-user"});
        if let Some(value) = patch {
            body["proxyPassword"] = value;
        }
        let response = client
            .put(format!("http://{address}/config/global-proxy"))
            .header("x-api-key", "offline-admin")
            .json(&body)
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        fixture.assert_persisted(Some(ProxyConfig {
            url: "http://127.0.0.1:31002".into(),
            username: Some("offline-user".into()),
            password: expected.map(str::to_owned),
        }));
    }
    let before = std::fs::read(fixture.path()).unwrap();
    let old = fixture.manager.proxy();
    for value in [
        serde_json::json!(42),
        serde_json::json!(false),
        serde_json::json!({}),
        serde_json::json!([]),
    ] {
        let response = client
            .put(format!("http://{address}/config/global-proxy"))
            .header("x-api-key", "offline-admin")
            .json(&serde_json::json!({"proxyUrl":"http://127.0.0.1:31003", "proxyPassword":value}))
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::UNPROCESSABLE_ENTITY);
        assert_eq!(fixture.manager.proxy(), old);
        assert_eq!(std::fs::read(fixture.path()).unwrap(), before);
    }
}

#[test]
fn persistence_diagnostics_identify_stage_and_error_category_without_raw_content() {
    let fixture = Fixture::new();
    std::fs::write(fixture.path(), r#"{"tlsBackend":"private-password"}"#).unwrap();
    let GlobalProxyUpdateError::Persistence(error) = fixture
        .manager
        .update_global_proxy(None, None, None)
        .unwrap_err()
    else {
        panic!("expected persistence error");
    };
    let diagnostics = config_update_diagnostics(&error);
    assert_eq!(diagnostics, ("reload", None, true));
    assert!(!format!("{diagnostics:?}").contains("private-password"));
    std::fs::remove_file(fixture.path()).unwrap();
    std::fs::remove_dir(&fixture.root).unwrap();
    let GlobalProxyUpdateError::Persistence(error) = fixture
        .manager
        .update_global_proxy(None, None, None)
        .unwrap_err()
    else {
        panic!("expected persistence error");
    };
    assert_eq!(
        config_update_diagnostics(&error),
        ("save", Some(std::io::ErrorKind::NotFound), false)
    );
    // Exercise another IO category without OS-specific filesystem permissions.
    let error: anyhow::Error = ConfigFileUpdateFailure {
        stage: "reload",
        source: std::io::Error::new(std::io::ErrorKind::PermissionDenied, "private-password")
            .into(),
    }
    .into();
    let diagnostics = config_update_diagnostics(&error);
    assert_eq!(
        diagnostics,
        ("reload", Some(std::io::ErrorKind::PermissionDenied), false)
    );
    assert!(!format!("{diagnostics:?}").contains("private-password"));
}

#[tokio::test]
async fn malformed_proxy_is_rejected_without_persisting_or_disclosing_credentials() {
    let fixture = Fixture::new();
    let service = AdminService::new(fixture.manager.clone(), Vec::new());
    let before = std::fs::read(fixture.path()).unwrap();
    let old = fixture.manager.proxy();
    for url in [
        "http://[",
        "http://127.0.0.1:99999",
        "socks5://[",
        "http://",
        "ftp://127.0.0.1:1080",
        "http://private-user:private-password@[",
    ] {
        let error = service
            .set_global_proxy(
                Some(url.into()),
                Some("private-user".into()),
                Some(Some("private-password".into())),
            )
            .unwrap_err();
        assert_eq!(error.status_code(), StatusCode::BAD_REQUEST);
        let response = error.into_http_response();
        let body = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let body = String::from_utf8(body.to_vec()).unwrap();
        assert!(!body.contains("private-user"));
        assert!(!body.contains("private-password"));
        assert_eq!(std::fs::read(fixture.path()).unwrap(), before);
        assert_eq!(fixture.manager.proxy(), old);
    }
}

#[test]
fn proxy_update_preserves_password_clear_and_supported_scheme_semantics() {
    let fixture = Fixture::new();
    for scheme in ["http", "https", "socks4", "socks4a", "socks5", "socks5h"] {
        let url = format!("{scheme}://127.0.0.1:31002");
        fixture
            .manager
            .update_global_proxy(Some(url.clone()), Some("next-user".into()), None)
            .unwrap();
        fixture.assert_persisted(Some(
            ProxyConfig::new(url).with_auth("next-user", "offline-old"),
        ));
    }
    for password in [Some(None), Some(Some(String::new()))] {
        fixture
            .manager
            .update_global_proxy(
                Some("http://127.0.0.1:31003".into()),
                Some(String::new()),
                password,
            )
            .unwrap();
        fixture.assert_persisted(Some(ProxyConfig::new("http://127.0.0.1:31003")));
    }
    fixture
        .manager
        .update_global_proxy(None, Some("unused".into()), Some(Some("unused".into())))
        .unwrap();
    fixture.assert_persisted(None);
    fixture
        .manager
        .update_global_proxy(Some("http://127.0.0.1:31004".into()), None, None)
        .unwrap();
    fixture.assert_persisted(Some(ProxyConfig::new("http://127.0.0.1:31004")));
}

#[tokio::test]
async fn persistence_failure_does_not_publish_proxy_or_acknowledge_success() {
    let fixture = Fixture::new();
    let old = fixture.manager.proxy();
    let service = AdminService::new(fixture.manager.clone(), Vec::new());
    // Reload failure must preserve the corrupt file, rather than overwrite it.
    std::fs::write(fixture.path(), "invalid json").unwrap();
    let error = service
        .set_global_proxy(Some("http://127.0.0.1:31002".into()), None, Some(None))
        .unwrap_err();
    assert_eq!(error.status_code(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(
        std::fs::read_to_string(fixture.path()).unwrap(),
        "invalid json"
    );
    assert_eq!(fixture.manager.proxy(), old);

    // A nonexistent parent permits loading defaults, but saving fails before
    // any bytes are written. This does not test arbitrary partial writes.
    std::fs::remove_file(fixture.path()).unwrap();
    std::fs::remove_dir(&fixture.root).unwrap();
    for url in [Some("http://127.0.0.1:31002".into()), None] {
        let error = service.set_global_proxy(url, None, Some(None)).unwrap_err();
        assert_eq!(error.status_code(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(fixture.manager.proxy(), old);
        assert!(!fixture.path().exists());
    }
}

#[test]
fn concurrent_proxy_updates_merge_after_previous_publication() {
    let fixture = Fixture::new();
    let manager = &fixture.manager;
    let old = manager.proxy();
    let file_guard = manager.config_write_lock.lock();
    let first_manager = manager.clone();
    let first = std::thread::spawn(move || {
        first_manager.update_global_proxy(
            Some("http://127.0.0.1:31002".into()),
            Some("first-user".into()),
            Some(Some("offline-new".into())),
        )
    });
    let deadline = Instant::now() + StdDuration::from_secs(5);
    while !manager.runtime_config_update_lock.is_locked() && Instant::now() < deadline {
        std::thread::yield_now();
    }
    let first_holds_update_lock = manager.runtime_config_update_lock.is_locked();
    let (started_tx, started_rx) = mpsc::channel();
    let (done_tx, done_rx) = mpsc::channel();
    let second_manager = manager.clone();
    let second = std::thread::spawn(move || {
        started_tx.send(()).unwrap();
        done_tx
            .send(second_manager.update_global_proxy(
                Some("http://127.0.0.1:31003".into()),
                Some("second-user".into()),
                None,
            ))
            .unwrap();
    });
    started_rx.recv_timeout(StdDuration::from_secs(5)).unwrap();
    let premature_result = done_rx.recv_timeout(StdDuration::from_millis(200)).ok();
    let second_was_blocked = premature_result.is_none();
    let before_publication = manager.proxy();
    // Another setting already persisted must survive both proxy writes.
    let mut fresh = Config::load(fixture.path()).unwrap();
    fresh.api_domain = ApiDomain::Kiro;
    fresh.save().unwrap();
    drop(file_guard);
    first.join().unwrap().unwrap();
    let second_result = premature_result
        .unwrap_or_else(|| done_rx.recv_timeout(StdDuration::from_secs(5)).unwrap());
    second.join().unwrap();
    second_result.unwrap();
    assert!(first_holds_update_lock);
    assert!(second_was_blocked);
    assert_eq!(before_publication, old);
    fixture.assert_persisted(Some(
        ProxyConfig::new("http://127.0.0.1:31003").with_auth("second-user", "offline-new"),
    ));
    assert_eq!(
        Config::load(fixture.path()).unwrap().api_domain,
        ApiDomain::Kiro
    );
}
