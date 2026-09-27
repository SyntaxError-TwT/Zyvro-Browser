//! Small C ABI around Brave's adblock-rust engine for Zyvro's Qt/Python UI.
//!
//! Qt WebEngine may call the request interceptor from Chromium IO threads, so
//! the adblock engine is compiled without adblock-rust's `single-thread`
//! optimization and guarded by a mutex.

use adblock::engine::Engine;
use adblock::lists::FilterSet;
use adblock::request::Request;
use adblock::resources::Resource;
use std::collections::HashSet;
use std::ffi::{CStr, CString, c_char};
use std::fs;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::{Mutex, OnceLock};

const ENGINE_VERSION: &[u8] = b"adblock-rust 0.13.3\0";
static LAST_ERROR: OnceLock<Mutex<String>> = OnceLock::new();

pub struct EngineHandle {
    engine: Mutex<Engine>,
    list_count: usize,
}

fn set_last_error(message: impl Into<String>) {
    let lock = LAST_ERROR.get_or_init(|| Mutex::new(String::new()));
    if let Ok(mut error) = lock.lock() {
        *error = message.into();
    }
}

unsafe fn c_string(value: *const c_char, label: &str) -> Result<String, String> {
    if value.is_null() {
        return Err(format!("{label} is null"));
    }
    // SAFETY: The caller promises a valid, NUL-terminated UTF-8 string.
    let text = unsafe { CStr::from_ptr(value) };
    text.to_str()
        .map(str::to_owned)
        .map_err(|_| format!("{label} is not valid UTF-8"))
}

fn list_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut files = Vec::new();
    let entries = fs::read_dir(root)
        .map_err(|error| format!("cannot read filter directory {}: {error}", root.display()))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("cannot read filter entry: {error}"))?;
        let path = entry.path();
        if path.is_file() && path.extension().and_then(|value| value.to_str()) == Some("txt") {
            files.push(path);
        }
    }
    files.sort();
    if files.is_empty() {
        return Err(format!("no .txt filter lists found in {}", root.display()));
    }
    Ok(files)
}

fn build_engine(
    filter_directory: &Path,
    resources_path: Option<&Path>,
) -> Result<EngineHandle, String> {
    let files = list_files(filter_directory)?;
    let mut filters = FilterSet::new(false);
    for path in &files {
        let text = fs::read_to_string(path)
            .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
        filters.add_filter_list(text, Default::default());
    }

    let mut engine = Engine::new_with_filter_set(filters);
    if let Some(path) = resources_path.filter(|path| path.is_file()) {
        let text = fs::read_to_string(path)
            .map_err(|error| format!("cannot read {}: {error}", path.display()))?;
        let resources: Vec<Resource> = serde_json::from_str(&text)
            .map_err(|error| format!("invalid resources JSON {}: {error}", path.display()))?;
        engine.use_resources(resources);
    }

    Ok(EngineHandle {
        engine: Mutex::new(engine),
        list_count: files.len(),
    })
}

fn owned_json<T: serde::Serialize>(value: &T) -> *mut c_char {
    match serde_json::to_string(value)
        .map_err(|error| error.to_string())
        .and_then(|text| CString::new(text).map_err(|error| error.to_string()))
    {
        Ok(text) => text.into_raw(),
        Err(error) => {
            set_last_error(error);
            ptr::null_mut()
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_version() -> *const c_char {
    ENGINE_VERSION.as_ptr().cast()
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_last_error() -> *mut c_char {
    let message = LAST_ERROR
        .get_or_init(|| Mutex::new(String::new()))
        .lock()
        .map(|value| value.clone())
        .unwrap_or_else(|_| "ad blocker error lock failed".to_string());
    CString::new(message)
        .unwrap_or_else(|_| CString::new("unknown ad blocker error").expect("static string"))
        .into_raw()
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_free_string(value: *mut c_char) {
    if !value.is_null() {
        // SAFETY: Pointers returned by this library come from CString::into_raw.
        unsafe { drop(CString::from_raw(value)) };
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_create(
    filter_directory: *const c_char,
    resources_path: *const c_char,
) -> *mut EngineHandle {
    let result = catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: Inputs are owned by the caller for the duration of this call.
        let filters = unsafe { c_string(filter_directory, "filter directory") }?;
        let resources = if resources_path.is_null() {
            None
        } else {
            Some(PathBuf::from(unsafe {
                c_string(resources_path, "resources path")
            }?))
        };
        build_engine(Path::new(&filters), resources.as_deref())
    }));
    match result {
        Ok(Ok(handle)) => Box::into_raw(Box::new(handle)),
        Ok(Err(error)) => {
            set_last_error(error);
            ptr::null_mut()
        }
        Err(_) => {
            set_last_error("adblock-rust panicked while creating the engine");
            ptr::null_mut()
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_destroy(handle: *mut EngineHandle) {
    if !handle.is_null() {
        // SAFETY: The pointer was created by Box::into_raw and is destroyed once.
        unsafe { drop(Box::from_raw(handle)) };
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_list_count(handle: *const EngineHandle) -> usize {
    if handle.is_null() {
        return 0;
    }
    // SAFETY: The caller keeps the handle alive for this call.
    unsafe { (*handle).list_count }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_check(
    handle: *const EngineHandle,
    url: *const c_char,
    source_url: *const c_char,
    request_type: *const c_char,
    method: *const c_char,
) -> i32 {
    if handle.is_null() {
        set_last_error("ad blocker handle is null");
        return -1;
    }
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<bool, String> {
        // SAFETY: Inputs remain valid during this synchronous call.
        let url = unsafe { c_string(url, "request URL") }?;
        let source = unsafe { c_string(source_url, "source URL") }?;
        let kind = unsafe { c_string(request_type, "request type") }?;
        let method = unsafe { c_string(method, "request method") }?;
        let request = Request::new(&url, &source, &kind, &method)
            .map_err(|error| format!("cannot parse request: {error:?}"))?;
        // SAFETY: The caller keeps the handle alive for this call.
        let guard = unsafe { &*handle }
            .engine
            .lock()
            .map_err(|_| "ad blocker engine lock failed".to_string())?;
        Ok(guard.check_network_request(&request).should_block())
    }));
    match result {
        Ok(Ok(true)) => 1,
        Ok(Ok(false)) => 0,
        Ok(Err(error)) => {
            set_last_error(error);
            -1
        }
        Err(_) => {
            set_last_error("adblock-rust panicked while checking a request");
            -1
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_cosmetic_json(
    handle: *const EngineHandle,
    url: *const c_char,
) -> *mut c_char {
    if handle.is_null() {
        set_last_error("ad blocker handle is null");
        return ptr::null_mut();
    }
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<_, String> {
        // SAFETY: Inputs remain valid during this synchronous call.
        let url = unsafe { c_string(url, "page URL") }?;
        // SAFETY: The caller keeps the handle alive for this call.
        let guard = unsafe { &*handle }
            .engine
            .lock()
            .map_err(|_| "ad blocker engine lock failed".to_string())?;
        Ok(guard.url_cosmetic_resources(&url))
    }));
    match result {
        Ok(Ok(resources)) => owned_json(&resources),
        Ok(Err(error)) => {
            set_last_error(error);
            ptr::null_mut()
        }
        Err(_) => {
            set_last_error("adblock-rust panicked while preparing cosmetic rules");
            ptr::null_mut()
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn zyvro_adblock_generic_json(
    handle: *const EngineHandle,
    classes_json: *const c_char,
    ids_json: *const c_char,
    exceptions_json: *const c_char,
) -> *mut c_char {
    if handle.is_null() {
        set_last_error("ad blocker handle is null");
        return ptr::null_mut();
    }
    let result = catch_unwind(AssertUnwindSafe(|| -> Result<Vec<String>, String> {
        // SAFETY: Inputs remain valid during this synchronous call.
        let classes: Vec<String> =
            serde_json::from_str(&unsafe { c_string(classes_json, "classes JSON")? })
                .map_err(|error| format!("invalid classes JSON: {error}"))?;
        let ids: Vec<String> = serde_json::from_str(&unsafe { c_string(ids_json, "ids JSON")? })
            .map_err(|error| format!("invalid ids JSON: {error}"))?;
        let exceptions: HashSet<String> =
            serde_json::from_str(&unsafe { c_string(exceptions_json, "exceptions JSON")? })
                .map_err(|error| format!("invalid exceptions JSON: {error}"))?;
        // SAFETY: The caller keeps the handle alive for this call.
        let guard = unsafe { &*handle }
            .engine
            .lock()
            .map_err(|_| "ad blocker engine lock failed".to_string())?;
        Ok(guard.hidden_class_id_selectors(classes, ids, &exceptions))
    }));
    match result {
        Ok(Ok(selectors)) => owned_json(&selectors),
        Ok(Err(error)) => {
            set_last_error(error);
            ptr::null_mut()
        }
        Err(_) => {
            set_last_error("adblock-rust panicked while matching generic selectors");
            ptr::null_mut()
        }
    }
}
