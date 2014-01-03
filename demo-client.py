
import os, sys, json, base64, urlparse
import requests
from hashlib import sha256
import hmac
from hkdf import HKDF
import binascii
import six
from six import binary_type, print_, int2byte

# PyPI has four candidates for PBKDF2 functionality. We use "simple-pbkdf2"
# by Armin Ronacher: https://pypi.python.org/pypi/simple-pbkdf2/1.0 . Note
# that v1.0 has a bug which causes segfaults when num_iterations is greater
# than about 88k.
from pbkdf2 import pbkdf2_bin

def makeRandom():
    return os.urandom(32)

def HMAC(key, msg):
    return hmac.new(key, msg, sha256).digest()

def printhex(name, value, groups_per_line=1):
    assert isinstance(value, binary_type), type(value)
    h = binascii.hexlify(value).decode("ascii")
    groups = [h[i:i+16] for i in range(0, len(h), 16)]
    lines = [" ".join(groups[i:i+groups_per_line])
             for i in range(0, len(groups), groups_per_line)]
    print_("%s:" % name)
    for line in lines:
        print_(line)
    print_()
def printdec(name, n):
    print_(name+" (base 10):")
    s = str(n)
    while len(s)%32:
        s = " "+s
    for i in range(0, len(s), 32):
        print_(s[i:i+32].replace(" ",""))
    print_()

def split(value):
    assert len(value)%32 == 0
    return [value[i:i+32] for i in range(0, len(value), 32)]
def KW(name):
    return b"identity.mozilla.com/picl/v1/" + six.b(name)
def KWE(name, emailUTF8):
    return b"identity.mozilla.com/picl/v1/" + six.b(name) + b":" + emailUTF8

def xor(s1, s2):
    assert isinstance(s1, binary_type), type(s1)
    assert isinstance(s2, binary_type), type(s2)
    assert len(s1) == len(s2)
    return b"".join([int2byte(ord(s1[i:i+1])^ord(s2[i:i+1])) for i in range(len(s1))])

#BASEURL = "http://127.0.0.1:9000/"
BASEURL = "https://api-accounts-onepw.dev.lcip.org/"
#BASEURL = "https://api-accounts-latest.dev.lcip.org/"

HOST = urlparse.urlparse(BASEURL)[1]

class WebError(Exception):
    def __init__(self, r):
        self.r = r
        self.args = (r, r.content)

def GET(api, versioned="v1/"):
    url = BASEURL+versioned+api
    print "GET", url
    r = requests.get(url)
    if r.status_code != 200:
        raise WebError(r)
    return r.json()

def POST(api, body={}, versioned="v1/"):
    url = BASEURL+versioned+api
    print "POST", url
    r = requests.post(url,
                      headers={"content-type": "application/json"},
                      data=json.dumps(body))
    if r.status_code != 200:
        raise WebError(r)
    return r.json()

from hawk import client as hawk_client

def HAWK_GET(api, id, key, versioned="v1/"):
    url = BASEURL+versioned+api
    print "HAWK_GET", url
    creds = {"id": id.encode("hex"),
             "key": key,
             "algorithm": "sha256"
             }
    header = hawk_client.header(url, "GET", {"credentials": creds,
                                             "ext": ""})
    r = requests.get(url, headers={"authorization": header["field"]})
    if r.status_code != 200:
        raise WebError(r)
    return r.json()

def HAWK_POST(api, id, key, body_object, versioned="v1/"):
    url = BASEURL+versioned+api
    print "HAWK_POST", url
    body = json.dumps(body_object)
    creds = {"id": id.encode("hex"),
             "key": key,
             "algorithm": "sha256"
             }
    header = hawk_client.header(url, "POST",
                                {"credentials": creds,
                                 "ext": "",
                                 "payload": body,
                                 "contentType": "application/json"})
    r = requests.post(url, headers={"authorization": header["field"],
                                    "content-type": "application/json"},
                      data=body)
    if r.status_code != 200:
        raise WebError(r)
    return r.json()

def stretch(emailUTF8, passwordUTF8, PBKDF2_rounds=1000):
    quickStretchedPW = pbkdf2_bin(passwordUTF8, KWE("quickStretch", emailUTF8),
                                  PBKDF2_rounds, keylen=1*32, hashfunc=sha256)
    printhex("quickStretchedPW", quickStretchedPW)
    authPW = HKDF(SKM=quickStretchedPW,
                  XTS="",
                  CTXinfo=KW("authPW"),
                  dkLen=1*32)
    unwrapBKey = HKDF(SKM=quickStretchedPW,
                      XTS="",
                      CTXinfo=KW("unwrapBkey"),
                      dkLen=1*32)
    printhex("authPW", authPW)
    printhex("unwrapBKey", unwrapBKey)
    return authPW, unwrapBKey

def processSessionToken(sessionToken):
    x = HKDF(SKM=sessionToken,
             dkLen=3*32,
             XTS=None,
             CTXinfo=KW("sessionToken"))
    tokenID, reqHMACkey, requestKey = split(x)
    return tokenID, reqHMACkey, requestKey

def getEmailStatus(sessionToken):
    tokenID, reqHMACkey, requestKey = processSessionToken(sessionToken)
    return HAWK_GET("recovery_email/status", tokenID, reqHMACkey)

def fetchKeys(keyFetchToken, unwrapBkey):
    x = HKDF(SKM=keyFetchToken,
             dkLen=3*32,
             XTS=None,
             CTXinfo=KW("keyFetchToken"))
    tokenID, reqHMACkey, keyRequestKey = split(x)
    y = HKDF(SKM=keyRequestKey,
             dkLen=32+2*32,
             XTS=None,
             CTXinfo=KW("account/keys"))
    respHMACkey = y[:32]
    respXORkey = y[32:]
    r = HAWK_GET("account/keys", tokenID, reqHMACkey)
    bundle = r["bundle"].decode("hex")
    ct,respMAC = bundle[:-32], bundle[-32:]
    respMAC2 = HMAC(respHMACkey, ct)
    assert respMAC2 == respMAC, (respMAC2.encode("hex"),
                                 respMAC.encode("hex"))
    kA, wrapKB = split(xor(ct, respXORkey))
    kB = xor(unwrapBkey, wrapKB)
    return kA, kB

def processChangePasswordToken(changePasswordToken):
    x = HKDF(SKM=changePasswordToken,
             dkLen=2*32,
             XTS=None,
             CTXinfo=KW("passwordChangeToken"))
    tokenID, reqHMACkey = split(x)
    return tokenID, reqHMACkey

def changePassword(emailUTF8, oldPassword, newPassword):
    oldAuthPW, oldunwrapBKey = stretch(emailUTF8, oldPassword)
    newAuthPW, newunwrapBKey = stretch(emailUTF8, newPassword)
    r = POST("password/change/start",
             {"email": emailUTF8,
              "oldAuthPW": oldAuthPW.encode("hex"),
              })
    print r
    keyFetchToken = r["keyFetchToken"].decode("hex")
    passwordChangeToken = r["passwordChangeToken"].decode("hex")
    kA, kB = fetchKeys(keyFetchToken, oldunwrapBKey)
    newWrapKB = xor(kB, newunwrapBKey)
    tokenID, reqHMACkey = processChangePasswordToken(passwordChangeToken)
    r = HAWK_POST("password/change/finish", tokenID, reqHMACkey,
                  {"authPW": newAuthPW.encode("hex"),
                   "wrapKb": newWrapKB.encode("hex"),
                   })
    print r
    assert r == {}, r
    print "password changed"

def signCertificate(sessionToken, pubkey, duration):
    tokenID, reqHMACkey, requestKey = processSessionToken(sessionToken)
    resp = HAWK_POST("certificate/sign", tokenID, reqHMACkey,
                     {"publicKey": pubkey, "duration": duration})
    assert resp["err"] is None
    return str(resp["cert"])

def b64parse(s_ascii):
    s_ascii += "="*((4 - len(s_ascii)%4)%4)
    return base64.urlsafe_b64decode(s_ascii)

def dumpCert(cert):
    pieces = cert.split(".")
    header = json.loads(b64parse(pieces[0]))
    payload = json.loads(b64parse(pieces[1]))
    print "header:", header
    print "payload:", payload
    return header, payload

def destroySession(sessionToken):
    tokenID, reqHMACkey, requestKey = processSessionToken(sessionToken)
    return HAWK_POST("session/destroy", tokenID, reqHMACkey, {})

def processForgotPasswordToken(passwordForgotToken):
    x = HKDF(SKM=passwordForgotToken,
             dkLen=2*32,
             XTS=None,
             CTXinfo=KW("passwordForgotToken"))
    # not listed in KeyServerProtocol document
    tokenID, reqHMACkey = split(x)
    return tokenID, reqHMACkey

def resendForgotPassword(passwordForgotToken, emailUTF8):
    tokenID, reqHMACkey = processForgotPasswordToken(passwordForgotToken)
    return HAWK_POST("password/forgot/resend_code", tokenID, reqHMACkey,
                     {"email": emailUTF8})

def verifyForgotPassword(passwordForgotToken, code):
    tokenID, reqHMACkey = processForgotPasswordToken(passwordForgotToken)
    r = HAWK_POST("password/forgot/verify_code", tokenID, reqHMACkey,
                  {"code": code})
    return r["accountResetToken"].decode("hex")

def main():
    GET("__heartbeat__", versioned="")
    command = sys.argv[1]
    if command in ("create", "login", "login-with-keys", "destroy"):
        emailUTF8, passwordUTF8 = sys.argv[2:4]
        printhex("email", emailUTF8)
        printhex("password", passwordUTF8)
    elif command == "change-password":
        emailUTF8, passwordUTF8, newPasswordUTF8 = sys.argv[2:5]
    elif command == "forgotpw-send":
        emailUTF8 = sys.argv[2]
    elif command == "forgotpw-resend":
        emailUTF8, passwordForgotToken_hex = sys.argv[2:4]
        passwordForgotToken = passwordForgotToken_hex.decode("hex")
    elif command == "forgotpw-submit":
        emailUTF8,passwordForgotToken_hex,code,newPasswordUTF8 = sys.argv[2:6]
        passwordForgotToken = passwordForgotToken_hex.decode("hex")
    else:
        raise NotImplementedError("unknown command '%s'" % command)

    assert isinstance(emailUTF8, binary_type)

    if command == "forgotpw-send":
        r = POST("password/forgot/send_code",
                 {"email": emailUTF8})
        print r
        passwordForgotToken = r["passwordForgotToken"]
        return

    if command == "forgotpw-resend":
        r = resendForgotPassword(passwordForgotToken, emailUTF8)
        print r
        return

    if command == "forgotpw-submit":
        newAuthPW = stretch(emailUTF8, newPasswordUTF8)[0]
        accountResetToken = verifyForgotPassword(passwordForgotToken, code)
        x = HKDF(SKM=accountResetToken,
                 XTS=None,
                 CTXinfo=KW("accountResetToken"),
                 dkLen=2*32)
        tokenID, reqHMACkey = split(x)
        r = HAWK_POST("account/reset", tokenID, reqHMACkey,
                      {"authPW": newAuthPW.encode("hex"),
                       })
        print r
        assert r == {}, r
        return

    assert command in ("create", "login", "login-with-keys", "destroy",
                       "change-password")

    authPW, unwrapBKey = stretch(emailUTF8, passwordUTF8)

    if command == "create":
        r = POST("account/create",
                 {"email": emailUTF8,
                  "authPW": authPW.encode("hex"),
                  })
        print r
        print "Now use the 'curl' command from the server logs to verify"
        return

    if command == "destroy":
        r = POST("account/destroy",
                 {"email": emailUTF8,
                  "authPW": authPW.encode("hex"),
                  })
        print r
        return

    if command == "change-password":
        newPasswordUTF8 = sys.argv[4]
        return changePassword(emailUTF8, passwordUTF8, newPasswordUTF8)

    assert command in ("login", "login-with-keys")
    getKeys = bool(command == "login-with-keys")

    r = POST("account/login?keys=true" if getKeys else "account/login",
             {"email": emailUTF8,
              "authPW": authPW.encode("hex"),
              })
    uid = str(r["uid"])
    sessionToken = r["sessionToken"].decode("hex")
    printhex("sessionToken", sessionToken)
    if getKeys:
        keyFetchToken = r["keyFetchToken"].decode("hex")
        printhex("keyFetchToken", keyFetchToken)

    email_status = getEmailStatus(sessionToken)
    print "email status:", email_status
    if email_status and getKeys:
        kA,kB = fetchKeys(keyFetchToken, unwrapBKey)
        printhex("kA", kA)
        printhex("kB", kB)

    if email_status:
        # exercise /certificate/sign . jwcrypto in the server demands that
        # "n" be of a recognized length (512 bits is the shortest it likes)
        pubkey = {"algorithm": "RS",
                  "n": "%d" % (2**512), "e": "2"}
        cert = signCertificate(sessionToken, pubkey, 24*3600*1000)
        print "cert:", cert
        header, payload = dumpCert(cert)
        assert header["alg"] == "RS256"
        assert payload["principal"]["email"] == "%s@%s" % (uid, HOST)
    # exercise /session/destroy
    print "destroying session now"
    print destroySession(sessionToken)
    print "session destroyed, this getEmailStatus should fail:"
    # check that the session is really gone
    try:
        getEmailStatus(sessionToken)
    except WebError as e:
        assert e.r.status_code == 401
        print e.r.content
        print " good, session really destroyed"
    else:
        print "bad, session not destroyed"
        assert 0

if __name__ == '__main__':
    main()

# exercised:
#  account/create
#  NO: account/devices (might not even be implemented)
#  account/keys
#  account/reset
#  account/destroy
#
#  account/login
#
#  session/destroy
#
#  recovery_email/status
#  NO: recovery_email/resend_code
#  NO: recovery_email/verify_code
#
#  certificate/sign
#
#  password/change/start
#  password/change/finish
#  password/forgot/send_code
#  password/forgot/resend_code
#  password/forgot/verify_code
#
#  NO: get_random_bytes
