package com.example.offers.api.clients

import io.micronaut.http.client.HttpClient

// TEST SCAFFOLDING: gives the "no HttpClient.create outside the factory" rule something to constrain.
// It lives in the factory file's package but not in VendorHttpClientKt, and only references the type.
class ClientUsage(@Suppress("unused") private val client: HttpClient)
