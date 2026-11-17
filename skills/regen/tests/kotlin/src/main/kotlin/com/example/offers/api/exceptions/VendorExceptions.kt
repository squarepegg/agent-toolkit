package com.example.offers.api.exceptions

// TEST SCAFFOLDING: stand-ins for the fictional Offers service's exception types.
class VendorUnreachableException(message: String, cause: Throwable? = null) : RuntimeException(message, cause)

class VendorAuthRejectedException(message: String) : RuntimeException(message)
