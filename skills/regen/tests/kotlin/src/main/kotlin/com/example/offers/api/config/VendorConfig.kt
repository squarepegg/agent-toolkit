package com.example.offers.api.config

// TEST SCAFFOLDING: stand-in for the fictional Offers service's production class, so the shipped
// examples compile. Not part of the skill. Defaults mirror src/main/resources/application.yml.
class VendorConfig {
    var baseUrl: String = "http://localhost"
    var timeoutMs: Long = 200
    var poolIdleTimeoutMs: Long = 30_000
    var maxConnectionRetries: Int = 1
}
