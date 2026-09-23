// Translucent true-size box for Layout mode.
//
// Deliberately NOT a copy of OmniTrackingMask: that one uses ZTest Always so
// a mask paints over passthrough. A ghost box is furniture-shaped and has to
// sit in the room, so it keeps normal depth testing and only drops ZWrite so
// the faces blend with each other.
//
// Lives in Resources/ because Shader.Find on a built-in returns null in a
// player build unless the shader is in Graphics > Always Included Shaders --
// the same trap that once made the mask overlay a white quad.
Shader "Omni/GhostBox"
{
    Properties
    {
        _Color ("Tint", Color) = (0.24, 0.86, 1, 0.28)
        _RimBoost ("Edge boost", Range(0, 2)) = 1.1
    }
    SubShader
    {
        Tags { "Queue" = "Transparent" "RenderType" = "Transparent" "IgnoreProjector" = "True" }
        Blend SrcAlpha OneMinusSrcAlpha
        Cull Off
        ZWrite Off

        Pass
        {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"

            struct appdata { float4 vertex : POSITION; float3 normal : NORMAL; };
            struct v2f
            {
                float4 pos : SV_POSITION;
                float3 normal : TEXCOORD0;
                float3 viewDir : TEXCOORD1;
            };

            fixed4 _Color;
            float _RimBoost;

            v2f vert (appdata v)
            {
                v2f o;
                o.pos = UnityObjectToClipPos(v.vertex);
                o.normal = UnityObjectToWorldNormal(v.normal);
                o.viewDir = WorldSpaceViewDir(mul(unity_ObjectToWorld, v.vertex));
                return o;
            }

            fixed4 frag (v2f i) : SV_Target
            {
                // Grazing faces read brighter, so the silhouette stays legible
                // against passthrough without drawing a wireframe.
                float facing = saturate(dot(normalize(i.normal), normalize(i.viewDir)));
                float rim = pow(1.0 - facing, 2.0) * _RimBoost;
                fixed4 c = _Color;
                c.rgb = saturate(c.rgb + c.rgb * rim);
                c.a = saturate(c.a + rim * 0.35);
                return c;
            }
            ENDCG
        }
    }
    Fallback Off
}
