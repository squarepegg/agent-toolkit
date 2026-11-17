package com.example.offers.restate

import com.example.offers.database.OfferStateWriter

// TEST SCAFFOLDING: the permitted caller of OfferStateWriter.
class RecordDecision(private val writer: OfferStateWriter) {
    fun record(playerId: String, offerId: String) = writer.write(playerId, offerId)
}
