//! Install recipes for the apps Orbit knows how to install.
pub mod nova;
pub mod orbit;

use crate::engine::Sources;
use crate::host::Host;
use crate::recipe::Recipe;
use std::sync::Arc;

pub fn load(host: &Host, sources: &Arc<Sources>) -> Vec<Arc<dyn Recipe>> {
    vec![
        Arc::new(nova::NovaRecipe::new(host.clone(), sources.clone())),
        Arc::new(orbit::OrbitRecipe::new(host.clone(), sources.clone(), None)),
    ]
}
