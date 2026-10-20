package com.example.voxtrap

object CallEvents {
    @Volatile
    var listener: CallEventListener? = null
}