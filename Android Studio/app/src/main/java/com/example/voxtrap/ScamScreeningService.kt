package com.example.voxtrap

import android.telecom.Call
import android.telecom.CallScreeningService
import android.util.Log

class ScamScreeningService : CallScreeningService() {
    override fun onScreenCall(details: Call.Details) {
        if (details.callDirection == Call.Details.DIRECTION_INCOMING) {
            Log.d("ScamScreen", "Incoming call detected")
            CallEvents.listener?.onIncomingCall()
        }
        respondToCall(details, CallResponse.Builder().build())
    }
}