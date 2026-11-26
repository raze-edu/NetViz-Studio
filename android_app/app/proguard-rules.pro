# Keep kotlinx.serialization runtime and generated serializers
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# Keep NetViz Studio v2.1.0 schema models and LAN Sync DTOs
-keep class org.netviz.survey.model.** { *; }
-keepclassmembers class org.netviz.survey.model.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep class org.netviz.survey.data.QrPairingPayload { *; }
-keep class org.netviz.survey.data.QrPairingPayload$* { *; }
-keep class org.netviz.survey.data.LanPingResponse { *; }
-keep class org.netviz.survey.data.LanPingResponse$* { *; }
-keep class org.netviz.survey.data.UdpDiscoveryReply { *; }
-keep class org.netviz.survey.data.UdpDiscoveryReply$* { *; }
-keep class org.netviz.survey.data.UploadSurveyResponse { *; }
-keep class org.netviz.survey.data.UploadSurveyResponse$* { *; }
